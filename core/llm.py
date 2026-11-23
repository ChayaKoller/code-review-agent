"""core/llm.py: השיחה עם המודל. שתי שאלות בלבד:

1. find_suspects: "באילו שורות בקובץ הזה יש באג?"
2. propose_fixes: "איך מתקנים את החשדות האלה?"

המודל מקבל את הקוד עם מספרי שורות (הוא לא סופר שורות טוב בעצמו) ומחזיר פלט מובנה
(pydantic), כך שאין צורך לפרק טקסט חופשי. הוא לא מקבל את הטסטים ולא צריך אותם.

Reviewer היא מחלקה כדי שבבדיקות אפשר להחליף אותה במודל מדומה (בלי רשת ובלי מכסה).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from core import config
from core.state import Edit, Suspect

BugType = Literal["off_by_one", "none_handling", "logic"]


class _SuspectOut(BaseModel):
    line: int = Field(description="line number of the bug, as shown at the start of the line")
    bug_type: BugType
    reason: str = Field(description="one short sentence: why this line is wrong")


class _SuspectsOut(BaseModel):
    suspects: list[_SuspectOut] = Field(default_factory=list)


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

File: {file}
{numbered}"""

_FIX_PROMPT = """You are fixing bugs in one Python file.
Each line starts with its line number and a colon.
Fix only the reported bugs, with the smallest change that fixes each one.
Do not change other lines, do not add features, do not reformat.
Each edit replaces lines start_line..end_line (inclusive) with new_code.
new_code must not contain line numbers. Edits must not overlap.

File: {file}
{numbered}

Reported bugs:
{bugs}
{note}"""


def number_lines(source: str) -> str:
    return "\n".join(f"{n}: {line}" for n, line in enumerate(source.splitlines(), start=1))


class Reviewer:
    """עוטף את המודל. נוצר פעם אחת ומשמש את כל הצמתים."""

    def __init__(self, model_name: str | None = None):
        # ייבוא כאן ולא למעלה: בבדיקות עם מודל מדומה לא צריך את הספרייה ולא מפתח
        from langchain_google_genai import ChatGoogleGenerativeAI

        model = ChatGoogleGenerativeAI(
            model=model_name or config.MODEL_NAME,
            temperature=config.LLM_TEMPERATURE,
            max_retries=config.LLM_MAX_RETRIES,
        )
        self._find = model.with_structured_output(_SuspectsOut)
        self._fix = model.with_structured_output(_EditsOut)

    def find_suspects(self, file: str, source: str) -> list[Suspect]:
        out = self._find.invoke(_FIND_PROMPT.format(file=file, numbered=number_lines(source)))
        total = len(source.splitlines())
        result: list[Suspect] = []
        for s in out.suspects if out else []:
            if 1 <= s.line <= total:  # מסננים מספרי שורה שהמודל המציא
                result.append({"file": file, "line": s.line, "bug_type": s.bug_type, "reason": s.reason})
        return result

    def propose_fixes(self, file: str, source: str, suspects: list[Suspect], note: str = "") -> list[Edit]:
        bugs = "\n".join(f"- line {s['line']} ({s['bug_type']}): {s['reason']}" for s in suspects)
        note_text = f"\nThe reviewer rejected the previous fix with this note, follow it:\n{note}" if note else ""
        out = self._fix.invoke(
            _FIX_PROMPT.format(file=file, numbered=number_lines(source), bugs=bugs, note=note_text)
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
