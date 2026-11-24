"""core/nodes.py: הצמתים של הגרף. כל צומת מקבל State ומחזיר רק את השדות שהוא משנה.

הסדר (הגרף עצמו ב-graph.py):
prepare -> select -> find_suspects -> baseline -> propose_fix -> screen_fixes -> review_fix (interrupt 1)
-> apply_fix -> verify_fix -> review_push (interrupt 2) -> publish -> finish

שלב 6: find_suspects מסנן (איחוד, סף ביטחון, מאמת, תקרה), ו-screen_fixes בודק כל תיקון
לבד מול הטסטים לפני שהמשתמשת רואה אותו.

הסוכן (המודל) רק קורא קוד ומחזיר חשדות ותיקונים. את הכתיבה להעתק, ה-commit
וה-push עושה קוד רגיל, ורק אחרי אישור. המקור לא נפתח לכתיבה בשום שלב.

ב-core אין print ואין input: השאלות למשתמשת עוברות דרך interrupt.
"""
from __future__ import annotations

import difflib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from langgraph.types import interrupt

from core import config, git_ops, publish
from core.docker_runner import DockerRunner, TestRun
from core.filtering import dedupe, rank, split_by_confidence
from core.repo_context import build_context
from core.llm import Reviewer
from core.sandbox import Sandbox, remove_sandbox
from core.selection import select_files
from core.state import (
    APPROVE,
    NO_PUSH,
    PUSH,
    REJECT,
    REJECT_WITH_NOTE,
    VERIFY_TESTS,
    Edit,
    ReviewState,
)
from core.verify import verify


@dataclass
class Deps:
    """מה שהצמתים צריכים מבחוץ. בבדיקות מחליפים במודל ו-Docker מדומים."""

    reviewer: Reviewer
    runner: DockerRunner = field(default_factory=DockerRunner)
    workspace_dir: str | None = None
    extra_packages: list[str] | None = None


@dataclass
class _SandboxView:
    """מה ש-verify צריך מה-sandbox. ה-Sandbox עצמו לא נשמר ב-State (לא ניתן לשמירה)."""

    path: Path
    has_tests: bool


def _read(state: ReviewState, rel: str) -> str:
    return (Path(state["sandbox_path"]) / rel).read_text(encoding="utf-8")


# ------------------------------------------------------------ תיקון בזיכרון


class EditError(ValueError):
    """המודל החזיר תיקון לא תקין (טווח שורות שגוי או חפיפה)."""


def apply_edits(source: str, edits: list[Edit]) -> str:
    """מחיל תיקונים על טקסט ומחזיר טקסט חדש. לא נוגע בדיסק."""
    lines = source.splitlines()
    ordered = sorted(edits, key=lambda e: e["start_line"], reverse=True)  # מהסוף להתחלה
    prev_start = len(lines) + 1
    for e in ordered:
        start, end = e["start_line"], e["end_line"]
        if not (1 <= start <= end <= len(lines)):
            raise EditError(f"{e['file']}: טווח שורות לא תקין {start}-{end}")
        if end >= prev_start:
            raise EditError(f"{e['file']}: תיקונים חופפים בשורות {start}-{end}")
        lines[start - 1 : end] = e["new_code"].splitlines()
        prev_start = start
    trailing = "\n" if source.endswith("\n") else ""
    return "\n".join(lines) + trailing


def make_diff(rel: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


def _by_file(items: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for item in items:
        grouped.setdefault(item["file"], []).append(item)
    return grouped


# ------------------------------------------------------------------- צמתים


def prepare(state: ReviewState, deps: Deps) -> dict:
    """יוצר את ההעתק (clone מקישור או העתקה מקומית) ופותח בו ענף חדש."""
    sb = Sandbox(state["repo_path"], workspace_dir=deps.workspace_dir).open()
    try:
        branch = ""
        if sb.is_git_repo:
            branch = git_ops.new_branch_name()
            git_ops.create_branch(sb.path, branch)
        return {
            "work_dir": str(sb.work_dir),
            "sandbox_path": str(sb.path),
            "branch_name": branch,
            "is_git_repo": sb.is_git_repo,
            "has_tests": sb.has_tests,
            "single_file": sb.source.name if sb.is_single_file else "",
            "attempts": 0,
        }
    except BaseException:
        sb.cleanup()
        raise


def select(state: ReviewState, deps: Deps) -> dict:
    # קובץ בודד: המשתמשת בחרה אותו במפורש, ולכן הוא נסקר גם אם הוא קצר
    include = [state["single_file"]] if state.get("single_file") else state.get("include")
    sel = select_files(state["sandbox_path"], include, state.get("exclude"))
    return {"files": sel.selected, "skipped": sel.skipped}


def _rejected(items: list[dict], why: str, stage: str) -> list[dict]:
    """why: הסבר למשתמשת. stage: קוד קבוע לשלב שבו החשד נזרק, למשפך ההערכה (eval/metrics.py)."""
    return [{**s, "rejected_because": why, "rejected_at": stage} for s in items]


def parallel_map(fn, items: list) -> list:
    """v12: מריץ fn על כל פריט במקביל, ומחזיר את התוצאות באותו סדר כמו items.
    מתאים לקריאות למודל: כל קריאה עצמאית, ורוב הזמן הולך על המתנה לרשת.
    חריגה בקריאה אחת נזרקת כאן, כמו בלולאה רגילה."""
    if config.MAX_PARALLEL_CALLS <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(config.MAX_PARALLEL_CALLS, len(items))) as pool:
        return list(pool.map(fn, items))


def find_suspects(state: ReviewState, deps: Deps) -> dict:
    """1. קריאה אחת למודל לכל קובץ (כל הקובץ, עם ציון ביטחון).
    2. סינון בקוד: איחוד כפילויות, סף ביטחון, דירוג.
    3. מאמת: קריאה נפרדת לכל חשד (רק הפונקציה). חשד בלי קלט מפיל נזרק.
    4. תקרה של MAX_SUSPECTS.
    חשדות שהמאמת זרק (בביטחון מעל הסף) נשמרים גם ב-doubtful_suspects: אם יש טסטים, הם מקבלים
    הזדמנות ב-screen_fixes, ונשארים רק אם הטסטים מאשרים (s6try2: המאמת זרק את הבאג של thefuck_25)."""
    sources = {rel: _read(state, rel) for rel in state["files"]}
    # ממצא 1: הקשר מהריפו, בלי מודל
    contexts = {rel: build_context(state["sandbox_path"], rel, src) for rel, src in sources.items()}
    # v12: הקבצים לא תלויים זה בזה, לכן הקריאות למודל נשלחות במקביל (ולא בזו אחר זו)
    per_file = parallel_map(lambda rel: deps.reviewer.find_suspects(rel, sources[rel], contexts[rel]), state["files"])
    raw = [s for found in per_file for s in found]
    unique = dedupe(raw)
    rejected = _rejected([s for s in raw if not any(s is u for u in unique)], "כפילות של חשד קרוב", "dedup")
    above, below = split_by_confidence(unique, config.SUSPECT_MIN_CONFIDENCE)
    rejected += _rejected(below, "ביטחון נמוך", "low_confidence")
    ranked = rank(above)
    rejected += _rejected(ranked[config.MAX_SUSPECTS_TO_VERIFY :], "מעבר לתקרת האימות", "cap")
    verified, doubtful = [], []
    to_check = ranked[: config.MAX_SUSPECTS_TO_VERIFY]
    # v12: גם המאמת במקביל. כל חשד נבדק לבד, והסדר נשמר (parallel_map מחזיר לפי הסדר)
    verdicts = parallel_map(lambda s: deps.reviewer.check_suspect(sources[s["file"]], s, contexts[s["file"]]), to_check)
    for s, verdict in zip(to_check, verdicts):
        checked = {**s, "failing_input": verdict["failing_input"], "expected": verdict["expected"],
                   "actual": verdict["actual"]}  # fmt: skip
        if verdict["is_bug"]:
            verified.append(checked)
        else:
            rejected += _rejected([checked], "המאמת לא מצא קלט שמפיל את הקוד", "checker")
            doubtful.append(checked)
    rejected += _rejected(verified[config.MAX_SUSPECTS :], "מעבר לתקרת החשדות", "cap")
    return {"raw_suspects": raw, "suspects": verified[: config.MAX_SUSPECTS], "rejected_suspects": rejected,
            "doubtful_suspects": doubtful[: config.MAX_DOUBTFUL]}  # fmt: skip


def baseline(state: ReviewState, deps: Deps) -> dict:
    """אם יש טסטים: מריצים אותם לפני התיקון, כדי לדעת מה נכשל כבר קודם."""
    if not state.get("has_tests"):
        return {"failing_tests": []}
    run = deps.runner.run_tests(state["sandbox_path"], extra_packages=deps.extra_packages)
    broken = run.timed_out or run.no_tests or bool(run.missing_modules)
    return {"failing_tests": run.failed, "baseline_broken": broken}


def propose_fix(state: ReviewState, deps: Deps) -> dict:
    """המודל מציע תיקון לכל קובץ שיש בו חשדות. התיקון עוד לא נכתב לדיסק: רק diff להצגה.
    תיקון לא תקין (טווח שורות שגוי, חפיפה) של קובץ אחד לא מפיל את כל הריצה: הקובץ מדולג."""
    note = state.get("rejection_note", "") if state.get("decision") == REJECT_WITH_NOTE else ""
    fixes: list[Edit] = []
    diffs = []
    screening = []
    groups = _by_file(state["suspects"])
    doubtful_files = []
    if state.get("has_tests") and _tests_usable(state):  # רק הטסטים יכולים "להציל" חשד שהמאמת זרק
        for rel, items in _by_file(state.get("doubtful_suspects", [])).items():
            if rel not in groups:
                groups[rel] = items
                doubtful_files.append(rel)
    for rel, items in groups.items():
        before = _read(state, rel)
        edits = deps.reviewer.propose_fixes(rel, before, items, note)
        if not edits:
            continue
        try:
            after = apply_edits(before, edits)  # בודק גם שהתיקון תקין
        except EditError as exc:
            screening.append({"file": rel, "status": "invalid_edit", "detail": str(exc), "attempts": 1})
            continue
        fixes.extend(edits)
        diffs.append(make_diff(rel, before, after))
    return {"fixes": fixes, "diff": "".join(diffs), "screening": screening, "doubtful_files": doubtful_files,
            "attempts": state.get("attempts", 0) + 1}  # fmt: skip


# ------------------------------------------------------------ screen_fixes

CONFIRMED = "tests_confirmed"  # התיקון גרם לטסט שנכשל לעבור, ולא שבר כלום
UNPROVEN = "unproven"  # לא שבר כלום, אבל גם לא תיקן טסט (או שאין טסטים)
BROKE = "broke_tests"  # שבר טסטים שעברו לפני (גם אחרי ניסיון שני)
NO_COMPILE = "syntax_error"


def failure_excerpt(output: str, test_ids: list[str], limit: int | None = None) -> str:
    """הקטע מפלט pytest שמתאר את הכשל הראשון: מהכותרת '___ test_name ___' שלו.
    אם אין כותרת כזו (למשל שגיאת collection): סוף הפלט."""
    limit = limit or config.FAILURE_FEEDBACK_CHARS
    for test_id in test_ids:
        name = test_id.split("::")[-1].split("[")[0]
        for marker in (f"_ {name}[", f"_ {name} _"):
            idx = output.find(marker)
            if idx != -1:
                start = output.rfind("\n", 0, idx) + 1
                return output[start : start + limit]
    return output[-limit:]


def _try_fix(state: ReviewState, deps: Deps, rel: str, before: str, after: str, can_test: bool) -> dict:
    """כותב את התיקון של קובץ אחד להעתק, בודק, ומחזיר את הקובץ למצבו. תמיד מחזיר."""
    path = Path(state["sandbox_path"]) / rel
    path.write_text(after, encoding="utf-8")
    try:
        if not can_test:
            comp = deps.runner.check_compile(state["sandbox_path"], [rel])
            return {"status": UNPROVEN if comp.ok else NO_COMPILE, "fixed_tests": [], "new_failures": [],
                    "output": "" if comp.ok else str(comp.errors)}  # fmt: skip
        run: TestRun = deps.runner.run_tests(state["sandbox_path"], extra_packages=deps.extra_packages)
    finally:
        path.write_text(before, encoding="utf-8")
    baseline = set(state.get("failing_tests", []))
    if run.timed_out:
        return {"status": BROKE, "fixed_tests": [], "new_failures": ["timeout"], "output": run.output}
    after_failed = set(run.failed)
    new = sorted(after_failed - baseline)
    fixed = sorted(baseline - after_failed)
    if new:
        return {"status": BROKE, "fixed_tests": [], "new_failures": new, "output": run.output}
    return {"status": CONFIRMED if fixed else UNPROVEN, "fixed_tests": fixed, "new_failures": [], "output": run.output}


def related_tests(rel: str, tests: list[str]) -> list[str]:
    """טסטים שנכשלו ב-baseline ושייכים לקובץ: לפי השם המקובל test_<שם הקובץ>.py.
    'thefuck/rules/mkdir_p.py' -> 'tests/rules/test_mkdir_p.py::...'"""
    name = f"test_{Path(rel).stem}.py"
    return [t for t in tests if t.split("::")[0].split("/")[-1] == name]


_RANK = {CONFIRMED: 3, UNPROVEN: 2, NO_COMPILE: 1, BROKE: 0}


def _feedback(result: dict, still_failing: list[str], edits: list[Edit]) -> str:
    """המשוב לניסיון הבא: מה קרה, פלט הטסט, והתיקון הקודם (המודל לא זוכר אותו)."""
    previous = "\n".join(f"lines {e['start_line']}-{e['end_line']}:\n{e['new_code']}" for e in edits)
    if result["status"] == BROKE:
        head = "Your previous fix broke tests that passed before."
        tests = result["new_failures"]
    else:
        head = "Your previous fix did not make this failing test pass."
        tests = still_failing
    return f"{head}\nPrevious fix:\n{previous}\nTest output:\n{failure_excerpt(result['output'], tests)}"


def screen_fixes(state: ReviewState, deps: Deps) -> dict:
    """בודק כל תיקון לבד (קובץ אחד בכל פעם) לפני שהמשתמשת רואה אותו.

    לבד, כי כשמריצים את כל התיקונים יחד לא יודעים מי שבר ומי תיקן (ב-before: תיקון אחד
    בקובץ נקי שבר טסט, וזה "הסתיר" גם תיקון נכון).
    - שבר טסט: ניסיון נוסף עם פלט הכשל (FIX_RETRIES).
    - לא שבר, אבל טסט של אותו קובץ שנכשל ב-baseline עדיין נכשל: גם כן ניסיון נוסף עם פלט הטסט
      (s6try1: ב-thefuck_1 הבאג נמצא והתיקון לא תיקן, והטסט הכשל היה שם ולא השתמשנו בו).
    - נשמר הניסיון הטוב ביותר: אושר בטסטים > לא הוכח > שבר.
    - אם יש תיקונים שאושרו בטסטים, רק הם נשארים. אחרת נשארים אלה שלא שברו (לא הוכחו).
    בלי טסטים: רק בדיקת קומפילציה לכל קובץ."""
    can_test = bool(state.get("has_tests")) and _tests_usable(state)
    note = state.get("rejection_note", "") if state.get("decision") == REJECT_WITH_NOTE else ""
    doubtful_files = set(state.get("doubtful_files", []))
    all_suspects = state["suspects"] + [s for s in state.get("doubtful_suspects", []) if s["file"] in doubtful_files]
    suspects_by_file = _by_file(all_suspects)
    report = list(state.get("screening", []))
    candidates: dict[str, tuple[list[Edit], str]] = {}  # קובץ -> (תיקונים, מצב)
    for rel, edits in _by_file(state["fixes"]).items():
        before = _read(state, rel)
        related = related_tests(rel, state.get("failing_tests", [])) if can_test else []
        attempt = 1
        result = _try_fix(state, deps, rel, before, apply_edits(before, edits), can_test)
        best = (result, edits)
        while attempt <= config.FIX_RETRIES:
            still = [t for t in related if t not in result["fixed_tests"]]
            if not (result["status"] == BROKE or (result["status"] == UNPROVEN and still)):
                break
            attempt += 1
            feedback = _feedback(result, still, edits)
            retry = deps.reviewer.propose_fixes(rel, before, suspects_by_file.get(rel, []), note, feedback)
            if not retry:
                break
            try:
                after = apply_edits(before, retry)
            except EditError:
                break
            edits = retry
            result = _try_fix(state, deps, rel, before, after, can_test)
            if _RANK[result["status"]] > _RANK[best[0]["status"]]:
                best = (result, edits)
        result, edits = best
        report.append({"file": rel, "status": result["status"], "fixed_tests": result["fixed_tests"],
                       "new_failures": result["new_failures"][:5], "attempts": attempt})  # fmt: skip
        if result["status"] == CONFIRMED or (result["status"] == UNPROVEN and rel not in doubtful_files):
            candidates[rel] = (edits, result["status"])  # חשד שהמאמת זרק נשאר רק אם הטסטים אישרו

    confirmed = {rel for rel, (_, st) in candidates.items() if st == CONFIRMED}
    keep = confirmed or set(candidates)
    fixes: list[Edit] = []
    diffs = []
    for rel in sorted(keep):
        edits = candidates[rel][0]
        before = _read(state, rel)
        fixes.extend(edits)
        diffs.append(make_diff(rel, before, apply_edits(before, edits)))
    status = {rel: st for rel, (_, st) in candidates.items()}
    kept_suspects = [{**s, "evidence": status[s["file"]]} for s in all_suspects if s["file"] in keep]
    dropped = [s for s in state["suspects"] if s["file"] not in keep]
    rescued = {(s["file"], s["line"]) for s in kept_suspects}
    why = {r["file"]: r["status"] for r in report}
    rejected = [r for r in state.get("rejected_suspects", []) if (r["file"], r["line"]) not in rescued] + [
        {**s, "rejected_at": "screening", "rejected_because": f"התיקון נפסל בבדיקה: {why.get(s['file'], 'לא הוצע תיקון')}"
         if s["file"] not in candidates else "תיקון אחר אושר בטסטים, וזה לא"}
        for s in dropped
    ]  # fmt: skip
    # doubtful מתרוקן: בלולאת reject_with_note לא מציעים שוב תיקון לחשד שהטסטים לא אישרו
    return {"fixes": fixes, "diff": "".join(diffs), "suspects": kept_suspects, "doubtful_suspects": [],
            "doubtful_files": [], "rejected_suspects": rejected, "screening": report}  # fmt: skip


def _tests_usable(state: ReviewState) -> bool:
    """baseline רץ ונתן תוצאה. אם הטסטים לא רצו בכלל (ספריות חסרות), אין טעם לסנן לפיהם."""
    return not state.get("baseline_broken", False)


def review_fix(state: ReviewState, deps: Deps) -> dict:
    """אישור ראשון. הגרף נעצר כאן עד שהמשתמשת עונה.
    התשובה: {"decision": "approve" | "reject" | "reject_with_note", "note": "..."}"""
    answer = interrupt(
        {
            "kind": "review_fix",
            "suspects": state["suspects"],
            "fixes": state["fixes"],
            "diff": state["diff"],
            "screening": state.get("screening", []),  # מה הטסטים אמרו על כל תיקון
            "attempt": state["attempts"],
            "max_attempts": config.MAX_REJECT_ATTEMPTS,
            "round": state.get("round", 1),  # v13
            "max_rounds": config.MAX_ROUNDS,
        }
    )
    decision = answer.get("decision")
    if decision not in (APPROVE, REJECT, REJECT_WITH_NOTE):
        raise ValueError(f"החלטה לא מוכרת: {decision}")
    return {"decision": decision, "rejection_note": answer.get("note", "")}


def apply_fix(state: ReviewState, deps: Deps) -> dict:
    """כותב את התיקון להעתק בלבד, ועושה commit בענף של הסוכן."""
    changed = []
    for rel, edits in _by_file(state["fixes"]).items():
        path = Path(state["sandbox_path"]) / rel
        path.write_text(apply_edits(path.read_text(encoding="utf-8"), edits), encoding="utf-8")
        changed.append(rel)
    sha = ""
    if state.get("is_git_repo"):
        lines = [f"- {e['file']}:{e['start_line']} {e['explanation']}" for e in state["fixes"]]
        message = "Fix bugs found by code-review-agent\n\n" + "\n".join(lines)
        sha = git_ops.commit_files(Path(state["sandbox_path"]), changed, message)
    # v13: מצטברים. כל סבב שאושר מוסיף commit לאותו ענף, ו-review_push מציג את כולם יחד
    return {"commit_sha": sha, "applied_fixes": state.get("applied_fixes", []) + state["fixes"],
            "applied_diff": state.get("applied_diff", "") + state["diff"]}  # fmt: skip


def verify_fix(state: ReviewState, deps: Deps) -> dict:
    """בודק את התיקון ב-Docker: טסטים אם יש, אחרת קומפילציה בלבד."""
    view = _SandboxView(Path(state["sandbox_path"]), bool(state.get("has_tests")))
    changed = sorted({e["file"] for e in state["fixes"]})
    v = verify(view, deps.runner, changed, extra_packages=deps.extra_packages)
    ok, summary, new_failures = v.ok, v.summary, []
    if v.level == VERIFY_TESTS and v.tests is not None and not v.tests.timed_out:
        before = set(state.get("failing_tests", []))
        after = set(v.tests.failed)
        new_failures = sorted(after - before)
        fixed = sorted(before - after)
        if after and not new_failures:
            ok = True  # כל מה שנכשל עכשיו נכשל גם לפני התיקון: התיקון לא שבר כלום
            summary += " כל הטסטים האלה נכשלו גם לפני התיקון, כלומר התיקון לא שבר אותם."
        if fixed:
            summary += f" התיקון גרם ל-{len(fixed)} טסטים שנכשלו קודם לעבור."
        if new_failures:
            summary += f" נשברו בגלל התיקון: {', '.join(new_failures[:5])}"
    return {
        "verification": v.level,
        "verify_ok": ok,
        "verify_summary": summary,
        "new_failures": new_failures,
        "failed_after": v.tests.failed if v.tests is not None else [],
    }


def next_round(state: ReviewState, deps: Deps) -> dict:
    """v13: סוגר סבב ופותח את הבא, על הקוד המתוקן שכבר בהעתק.
    הטסטים שנכשלו אחרי התיקון הם ה-baseline של הסבב הבא (בלי הרצה נוספת של הטסטים).
    החשדות של הסבב שהסתיים עוברים לשדות prev_*, ושדות הסבב מתאפסים."""
    summary = {
        "round": state.get("round", 1),
        "fixed_files": sorted({e["file"] for e in state.get("fixes", [])}),
        "failing_before": len(state.get("failing_tests", [])),
        "failing_after": len(state.get("failed_after", [])),
    }
    return {
        "round": state.get("round", 1) + 1,
        "rounds": state.get("rounds", []) + [summary],
        "failing_tests": state.get("failed_after", []),
        "prev_suspects": state.get("prev_suspects", []) + state.get("suspects", []),
        "prev_raw_suspects": state.get("prev_raw_suspects", []) + state.get("raw_suspects", []),
        "prev_rejected_suspects": state.get("prev_rejected_suspects", []) + state.get("rejected_suspects", []),
        "prev_screening": state.get("prev_screening", []) + state.get("screening", []),
        "suspects": [], "raw_suspects": [], "rejected_suspects": [], "doubtful_suspects": [], "doubtful_files": [],
        "fixes": [], "diff": "", "screening": [], "decision": "", "rejection_note": "", "attempts": 0,
    }  # fmt: skip


def _merged(state: ReviewState) -> dict:
    """v13: בסוף הריצה, השדות שהדוח וההערכה קוראים מאחדים את כל הסבבים."""
    if state.get("round", 1) == 1:
        return {}
    return {
        "suspects": state.get("prev_suspects", []) + state.get("suspects", []),
        "raw_suspects": state.get("prev_raw_suspects", []) + state.get("raw_suspects", []),
        "rejected_suspects": state.get("prev_rejected_suspects", []) + state.get("rejected_suspects", []),
        "screening": state.get("prev_screening", []) + state.get("screening", []),
        "fixes": state.get("applied_fixes", []),
        "diff": state.get("applied_diff", ""),
    }


def review_push(state: ReviewState, deps: Deps) -> dict:
    """אישור שני: אחרי שרואים את תוצאת הבדיקה. התשובה: {"decision": "push" | "no_push"}"""
    answer = interrupt(
        {
            "kind": "review_push",
            "branch": state["branch_name"],
            "verification": state["verification"],
            "verify_ok": state["verify_ok"],
            "summary": state["verify_summary"],
            "diff": state.get("applied_diff") or state["diff"],  # v13: כל התיקונים מכל הסבבים
            "round": state.get("round", 1),
            "rounds": state.get("rounds", []),
        }
    )
    decision = answer.get("decision")
    if decision not in (PUSH, NO_PUSH):
        raise ValueError(f"החלטה לא מוכרת: {decision}")
    return {"push_decision": decision}


def publish_fix(state: ReviewState, deps: Deps) -> dict:
    try:
        message = publish.push_branch(state["sandbox_path"], state["branch_name"])
        return {"pushed": True, "result": message}
    except publish.PublishError as exc:
        return {"pushed": False, "result": f"{exc}\nה-diff נשמר בתוצאה, ואפשר להחיל אותו ידנית."}


def finish(state: ReviewState, deps: Deps) -> dict:
    """תמיד הצומת האחרון: מוחק את ההעתק ומסכם."""
    if state.get("work_dir"):
        remove_sandbox(state["work_dir"])
    merged = _merged(state)
    if state.get("result"):
        return merged
    if merged:  # v13: יותר מסבב אחד
        n = len(state.get("rounds", [])) + 1
        result = f"הסתיימו {n} סבבים, ואושרו {len(merged['fixes'])} תיקונים."
        if state.get("push_decision") == NO_PUSH:
            result += " התיקונים לא נדחפו (לפי ההחלטה שלך). ה-diff בתוצאה."
        elif not state.get("is_git_repo"):
            result += " אין .git, ולכן אין דחיפה: ה-diff בתוצאה."
        return {**merged, "result": result}
    if not state.get("suspects") and state.get("rejected_suspects") and not state.get("screening"):
        result = f"המודל העלה {len(state['rejected_suspects'])} חשדות, וכולם נזרקו בסינון. לא נמצאו באגים."
    elif not state.get("suspects") and not state.get("screening"):
        result = "לא נמצאו חשדות לבאגים."
    elif not state.get("fixes"):
        result = "נמצאו חשדות, אבל אף תיקון לא עבר את הבדיקה (או שלא הוצע תיקון)."
    elif state.get("decision") != APPROVE:
        result = "התיקון נדחה. לא נכתב שום דבר."
    elif not state.get("is_git_repo"):
        result = "התיקון נבדק. אין .git (קובץ בודד), ולכן אין דחיפה: ה-diff בתוצאה."
    else:
        result = "התיקון לא נדחף (לפי ההחלטה שלך). ה-diff בתוצאה."
    return {"result": result}
