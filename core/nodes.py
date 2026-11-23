"""core/nodes.py: הצמתים של הגרף. כל צומת מקבל State ומחזיר רק את השדות שהוא משנה.

הסדר (הגרף עצמו ב-graph.py):
prepare -> select -> find_suspects -> baseline -> propose_fix -> review_fix (interrupt 1)
-> apply_fix -> verify_fix -> review_push (interrupt 2) -> publish -> finish

הסוכן (המודל) רק קורא קוד ומחזיר חשדות ותיקונים. את הכתיבה להעתק, ה-commit
וה-push עושה קוד רגיל, ורק אחרי אישור. המקור לא נפתח לכתיבה בשום שלב.

ב-core אין print ואין input: השאלות למשתמשת עוברות דרך interrupt.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from pathlib import Path

from langgraph.types import interrupt

from core import config, git_ops, publish
from core.docker_runner import DockerRunner
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


def find_suspects(state: ReviewState, deps: Deps) -> dict:
    """קריאה אחת למודל לכל קובץ. המודל מקבל רק את הקוד."""
    suspects = []
    for rel in state["files"]:
        suspects.extend(deps.reviewer.find_suspects(rel, _read(state, rel)))
    return {"suspects": suspects}


def baseline(state: ReviewState, deps: Deps) -> dict:
    """אם יש טסטים: מריצים אותם לפני התיקון, כדי לדעת מה נכשל כבר קודם."""
    if not state.get("has_tests"):
        return {"failing_tests": []}
    run = deps.runner.run_tests(state["sandbox_path"], extra_packages=deps.extra_packages)
    return {"failing_tests": run.failed}


def propose_fix(state: ReviewState, deps: Deps) -> dict:
    """המודל מציע תיקונים לכל החשדות. התיקון עוד לא נכתב לדיסק: רק diff להצגה."""
    note = state.get("rejection_note", "") if state.get("decision") == REJECT_WITH_NOTE else ""
    fixes: list[Edit] = []
    diffs = []
    for rel, items in _by_file(state["suspects"]).items():
        before = _read(state, rel)
        edits = deps.reviewer.propose_fixes(rel, before, items, note)
        if not edits:
            continue
        after = apply_edits(before, edits)  # בודק גם שהתיקון תקין
        fixes.extend(edits)
        diffs.append(make_diff(rel, before, after))
    return {"fixes": fixes, "diff": "".join(diffs), "attempts": state.get("attempts", 0) + 1}


def review_fix(state: ReviewState, deps: Deps) -> dict:
    """אישור ראשון. הגרף נעצר כאן עד שהמשתמשת עונה.
    התשובה: {"decision": "approve" | "reject" | "reject_with_note", "note": "..."}"""
    answer = interrupt(
        {
            "kind": "review_fix",
            "suspects": state["suspects"],
            "fixes": state["fixes"],
            "diff": state["diff"],
            "attempt": state["attempts"],
            "max_attempts": config.MAX_REJECT_ATTEMPTS,
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
    return {"commit_sha": sha}


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
            "diff": state["diff"],
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
    if state.get("result"):
        return {}
    if not state.get("suspects"):
        result = "לא נמצאו חשדות לבאגים."
    elif not state.get("fixes"):
        result = "נמצאו חשדות, אבל המודל לא הציע תיקון."
    elif state.get("decision") != APPROVE:
        result = "התיקון נדחה. לא נכתב שום דבר."
    elif not state.get("is_git_repo"):
        result = "התיקון נבדק. אין .git (קובץ בודד), ולכן אין דחיפה: ה-diff בתוצאה."
    else:
        result = "התיקון לא נדחף (לפי ההחלטה שלך). ה-diff בתוצאה."
    return {"result": result}
