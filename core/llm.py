"""core/llm.py: השיחה עם המודל. שלוש שאלות:

1. find_suspects: "באילו שורות בקובץ הזה יש באג?" (כל הקובץ, עם ציון ביטחון 1-5)
2. check_suspect: "תן קלט קונקרטי שמפיל את הקוד הזה" (רק הפונקציה). זה המאמת של שלב 6:
   חשד בלי קלט מפיל, או שהמאמת אומר שהקוד נכון, נזרק. כך "חשד" צריך לעבור שתי קריאות נפרדות.
3. propose_fixes: "איך מתקנים את החשדות האלה?" (רק הפונקציות, ואם תיקון קודם שבר טסט, גם פלט הכשל)

שלב 6, למה לא כל הקובץ בכל קריאה: למודל אין זיכרון בין קריאות, ולכן כל קריאה שולחת הכול מחדש.
כך נראה ב-LangSmith "פרומפט ארוך שחוזר". באימות ובתיקון שולחים רק את הפונקציה (core/code_context.py).

המודל מקבל את הקוד עם מספרי שורות (הוא לא סופר שורות טוב בעצמו) ומחזיר פלט מובנה
(pydantic), כך שאין צורך לפרק טקסט חופשי. הוא לא מקבל את הטסטים ולא צריך אותם.

Reviewer היא מחלקה כדי שבבדיקות אפשר להחליף אותה במודל מדומה (בלי רשת ובלי מכסה).
"""
from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel, Field

from core import config
from core.code_context import excerpt
from core.state import Edit, Suspect

# שגיאות זמניות של השרת: עומס (503) או מכסה לדקה (429). שווה לחכות ולנסות שוב
# שגיאות זמניות שכדאי לנסות שוב. timeout / DEADLINE: הקריאה לא הסתיימה בזמן (LLM_TIMEOUT_SECONDS)
_TEMPORARY = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "overloaded", "504", "DEADLINE_EXCEEDED", "timed out", "Timeout")


class LLMUnavailable(RuntimeError):
    """המודל לא זמין גם אחרי כמה ניסיונות."""


BugType = Literal["off_by_one", "none_handling", "logic"]


class _SuspectOut(BaseModel):
    # הסדר חשוב (ממצא 4): המודל כותב את השדות לפי הסדר. קודם הנימוק, ורק אחריו מספר השורה,
    # כדי שהשורה תיבחר אחרי שהמודל ניסח מה לא בסדר, ולא לפני.
    reason: str = Field(description="one short sentence: why this code is wrong, for which input")
    bug_type: BugType
    line: int = Field(description="line number of the bug, as shown at the start of the line")
    confidence: int = Field(
        description="1-5: 5 = certain this line gives a wrong result for a normal input, "
        "1 = only a defensive check for a case that may never happen"
    )


class _SuspectsOut(BaseModel):
    suspects: list[_SuspectOut] = Field(default_factory=list)


class _VerdictOut(BaseModel):
    failing_input: str = Field(description="a concrete call or input that makes this code misbehave, or empty")
    expected: str = Field(description="what the correct result for that input is")
    actual: str = Field(description="what this code actually returns or raises for that input")
    is_bug: bool = Field(description="true only if actual != expected for a realistic input")


class _EditOut(BaseModel):
    start_line: int = Field(description="first line to replace (inclusive)")
    end_line: int = Field(description="last line to replace (inclusive)")
    new_code: str = Field(description="the replacement code for these lines, with correct indentation")
    explanation: str = Field(description="one short sentence: what the fix changes")


class _EditsOut(BaseModel):
    edits: list[_EditOut] = Field(default_factory=list)


_FIND_PROMPT = """You are reviewing one Python file for bugs.
Each line starts with its line number and a colon.
Report only real bugs of these kinds: off-by-one, missing None / missing value handling, wrong logic.
Do not report style, naming, performance, security or missing features.
If the file has no such bug, return an empty list.
{context}
File: {file}
{numbered}"""

_CHECK_PROMPT = """Another reviewer claims there is a bug in this Python code. Try to prove it.
Give one concrete, realistic input (or call) for which the code misbehaves, the expected result,
and what the code actually does. Trace the code line by line for that input.
If you cannot find such an input, return is_bug=false and leave failing_input empty.
A missing check for a value that the callers never pass is not a bug.

{context}
File: {file} (only the relevant part; line numbers are from the full file)
{numbered}

Claim: line {line} ({bug_type}): {reason}"""

_FIX_PROMPT = """You are fixing bugs in one Python file.
Each line starts with its line number and a colon (only the relevant parts of the file are shown).
Fix only the reported bugs, with the smallest change that fixes each one.
Do not change other lines, do not add features, do not reformat.
Each edit replaces lines start_line..end_line (inclusive) with new_code.
new_code must not contain line numbers. Edits must not overlap.

File: {file}
{numbered}

Reported bugs:
{bugs}
{note}{failure}"""


def number_lines(source: str) -> str:
    return "\n".join(f"{n}: {line}" for n, line in enumerate(source.splitlines(), start=1))


class Reviewer:
    """עוטף את המודל. נוצר פעם אחת ומשמש את כל הצמתים."""

    def __init__(self, model_name: str | None = None):
        # ייבוא כאן ולא למעלה: בבדיקות עם מודל מדומה לא צריך את הספרייה ולא מפתח
        from langchain_google_genai import ChatGoogleGenerativeAI

        def make(name: str):
            return ChatGoogleGenerativeAI(
                model=name,
                temperature=config.LLM_TEMPERATURE,
                max_retries=config.LLM_MAX_RETRIES,
                timeout=config.LLM_TIMEOUT_SECONDS,
            )

        model = make(model_name or config.MODEL_NAME)
        checker = make(config.CHECK_MODEL_NAME) if config.CHECK_MODEL_NAME else model
        # include_raw: מקבלים גם את התשובה הגולמית, כדי לספור טוקנים (עלות)
        self._find = model.with_structured_output(_SuspectsOut, include_raw=True)
        self._check = checker.with_structured_output(_VerdictOut, include_raw=True)
        self._fix = model.with_structured_output(_EditsOut, include_raw=True)
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def _invoke(self, chain, prompt: str):
        delay = config.LLM_RETRY_FIRST_DELAY
        for attempt in range(1, config.LLM_BUSY_RETRIES + 1):
            try:
                out = chain.invoke(prompt)
                break
            except Exception as exc:  # noqa: BLE001 - בודקים לפי הטקסט אם השגיאה זמנית
                if not any(code in str(exc) for code in _TEMPORARY):
                    raise
                if attempt == config.LLM_BUSY_RETRIES:
                    raise LLMUnavailable(
                        f"המודל {config.MODEL_NAME} עמוס או שנגמרה המכסה לדקה (ניסיתי {attempt} פעמים). "
                        "לנסות שוב בעוד כמה דקות, או להחליף את MODEL_NAME ב-.env"
                    ) from exc
                time.sleep(delay)
                delay *= 2
        self.calls += 1
        usage = getattr(out.get("raw"), "usage_metadata", None) or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        if out.get("parsing_error"):
            raise ValueError(f"המודל החזיר פלט שלא תואם למבנה: {out['parsing_error']}")
        return out.get("parsed")

    def find_suspects(self, file: str, source: str, context: str = "") -> list[Suspect]:
        """context: חתימות של מה שהקובץ מייבא מהריפו (core/repo_context.py). ריק = בלי הקשר."""
        prompt = _FIND_PROMPT.format(file=file, numbered=number_lines(source), context=_context_block(context))
        out = self._invoke(self._find, prompt)
        total = len(source.splitlines())
        result: list[Suspect] = []
        for s in out.suspects if out else []:
            if 1 <= s.line <= total:  # מסננים מספרי שורה שהמודל המציא
                result.append({"file": file, "line": s.line, "bug_type": s.bug_type, "reason": s.reason,
                               "confidence": max(1, min(5, s.confidence))})  # fmt: skip
        return result

    def check_suspect(self, source: str, suspect: Suspect, context: str = "") -> dict:
        """המאמת. מחזיר {"is_bug", "failing_input", "expected", "actual"}.
        חשד נחשב מאומת רק אם is_bug וגם יש קלט מפיל (לא ריק)."""
        prompt = _CHECK_PROMPT.format(
            file=suspect["file"], numbered=excerpt(source, [suspect["line"]]), line=suspect["line"],
            context=_context_block(context),
            bug_type=suspect["bug_type"], reason=suspect["reason"],
        )  # fmt: skip
        out = self._invoke(self._check, prompt)
        if out is None:
            return {"is_bug": False, "failing_input": "", "expected": "", "actual": ""}
        has_input = bool(out.failing_input.strip())
        return {"is_bug": bool(out.is_bug and has_input), "failing_input": out.failing_input,
                "expected": out.expected, "actual": out.actual}  # fmt: skip

    def propose_fixes(
        self, file: str, source: str, suspects: list[Suspect], note: str = "", failure: str = ""
    ) -> list[Edit]:
        """failure: משוב מהטסטים על הניסיון הקודם (הלולאה של screen_fixes), כולל הכותרת שמסבירה
        מה קרה (שבר טסט / לא תיקן טסט שנכשל) ואת התיקון הקודם. ריק בניסיון הראשון."""
        bugs = "\n".join(_describe(s) for s in suspects)
        note_text = f"\nThe reviewer rejected the previous fix with this note, follow it:\n{note}" if note else ""
        failure_text = (
            f"\n{failure}\nWrite a different fix. Use the test output to see what the code must return." if failure else ""
        )
        numbered = excerpt(source, [s["line"] for s in suspects])
        out = self._invoke(
            self._fix,
            _FIX_PROMPT.format(file=file, numbered=numbered, bugs=bugs, note=note_text, failure=failure_text),
        )
        return [
            {
                "file": file,
                "start_line": e.start_line,
                "end_line": e.end_line,
                "new_code": e.new_code,
                "explanation": e.explanation,
            }
            for e in (out.edits if out else [])
        ]


def _describe(s: Suspect) -> str:
    text = f"- line {s['line']} ({s['bug_type']}): {s['reason']}"
    if s.get("failing_input"):  # מה שהמאמת מצא עוזר לתקן: קלט, צפוי, בפועל
        text += f"\n  failing input: {s['failing_input']} | expected: {s.get('expected', '')} | actual: {s.get('actual', '')}"
    return text


def _context_block(context: str) -> str:
    if not context:
        return ""
    return (
        "\nDefinitions this file imports from the same repository (signatures only, for reference;"
        " do not review them):\n" + context + "\n"
    )
