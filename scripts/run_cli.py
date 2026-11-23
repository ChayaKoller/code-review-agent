"""scripts/run_cli.py: הרצת הסוכן מהטרמינל. כאן (ולא ב-core) מותר print ו-input.

מריצים מהשורש:
    python -m scripts.run_cli https://github.com/user/repo
    python -m scripts.run_cli C:\\repos\\my-repo --include app/utils.py
    python -m scripts.run_cli C:\\code\\script.py --workspace D:\\work
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from langgraph.types import Command

from core import config as config_module
from core.graph import build_graph
from core.llm import LLMUnavailable, Reviewer
from core.nodes import Deps
from core.sandbox import remove_sandbox

PATCH_FILE = "cra_fix.patch"
REPORT_FILE = "cra_report.md"  # סיכום ההרצה, קל להעתיק ולהדביק


def write_report(values: dict, source: str, reviewer: Reviewer, error: str = "") -> Path:
    """כותב את כל מה שקרה בהרצה לקובץ markdown אחד: חשדות, diff, בדיקה, החלטות ועלות."""
    lines = [f"# דוח הרצה: {source}", "", f"- מודל: `{config_module.MODEL_NAME}`"]
    lines.append(f"- קריאות למודל: {reviewer.calls}, טוקנים: {reviewer.input_tokens} קלט + {reviewer.output_tokens} פלט")
    if error:
        lines += ["", "## שגיאה", "```", error, "```"]
    lines += ["", "## קבצים", f"- נסקרו: {', '.join(values.get('files', [])) or 'אין'}"]
    for name, why in (values.get("skipped") or {}).items():
        lines.append(f"- דולג: `{name}` ({why})")
    lines += ["", "## חשדות (מה המודל מצא)"]
    for x in values.get("suspects") or []:
        lines.append(f"- `{x['file']}:{x['line']}` [{x['bug_type']}] {x['reason']}")
    if not values.get("suspects"):
        lines.append("- אין")
    if values.get("fixes"):
        lines += ["", "## תיקונים (מה המודל הציע)"]
        for e in values["fixes"]:
            lines.append(f"- `{e['file']}:{e['start_line']}-{e['end_line']}` {e['explanation']}")
        lines += ["", "```diff", values.get("diff", "").rstrip(), "```"]
    if values.get("has_tests") and "failing_tests" in values:
        lines += ["", "## טסטים", f"- נכשלו לפני התיקון: {len(values['failing_tests'])}"]
        if "failed_after" in values:
            lines.append(f"- נכשלו אחרי התיקון: {len(values['failed_after'])}")
            lines.append(f"- נשברו בגלל התיקון: {', '.join(values.get('new_failures', [])) or 'אף אחד'}")
    if values.get("verify_summary"):
        lines += ["", "## בדיקה", f"- סוג: {values.get('verification')}", f"- {values['verify_summary']}"]
    lines += ["", "## החלטות", f"- אישור ראשון: {values.get('decision', '-')}"]
    if values.get("rejection_note"):
        lines.append(f"- הערה: {values['rejection_note']}")
    lines.append(f"- אישור שני: {values.get('push_decision', '-')}")
    lines += ["", "## תוצאה", values.get("result", "") or "-", ""]
    path = Path(REPORT_FILE)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _ask_fix(info: dict) -> dict:
    print(f"\n=== חשדות לבאגים (ניסיון {info['attempt']} מתוך {info['max_attempts']}) ===")
    for s in info["suspects"]:
        print(f"  {s['file']}:{s['line']} [{s['bug_type']}] {s['reason']}")
    print("\n=== התיקון המוצע ===")
    print(info["diff"])
    while True:
        choice = input("approve / reject / note? ").strip().lower()
        if choice == "approve":
            return {"decision": "approve"}
        if choice == "reject":
            return {"decision": "reject"}
        if choice == "note":
            return {"decision": "reject_with_note", "note": input("הערה למודל: ")}


def _ask_push(info: dict) -> dict:
    print("\n=== תוצאת הבדיקה ===")
    print(("✓ " if info["verify_ok"] else "✗ ") + info["summary"])
    if info["verification"] == "compile_only":
        print("שימי לב: לא נבדק בטסטים.")
    while True:
        choice = input(f"לדחוף לענף {info['branch']}? push / no: ").strip().lower()
        if choice in ("push", "no"):
            return {"decision": "push" if choice == "push" else "no_push"}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Code Review Agent")
    parser.add_argument("source", help="קישור לגיט, תיקייה או קובץ .py")
    parser.add_argument("--include", nargs="*", help="רק הקבצים האלה")
    parser.add_argument("--exclude", nargs="*", help="בלי הקבצים האלה")
    parser.add_argument("--workspace", help="תיקיית אב להעתק (במקום התיקייה הזמנית)")
    parser.add_argument("--extra-packages", nargs="*", help="חבילות נוספות ל-image של הריפו")
    args = parser.parse_args(argv)

    reviewer = Reviewer()
    deps = Deps(reviewer=reviewer, workspace_dir=args.workspace, extra_packages=args.extra_packages)
    graph = build_graph(deps)
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    state = {"repo_path": args.source, "include": args.include or [], "exclude": args.exclude or []}

    print("מכינה העתק, בוחרת קבצים ושולחת למודל...")
    error = ""
    try:
        result = graph.invoke(state, config)
        while "__interrupt__" in result:
            info = result["__interrupt__"][0].value
            if info["kind"] == "review_push":
                answer = _ask_push(info)
            else:
                answer = _ask_fix(info)
                if answer["decision"] == "approve":
                    print("מחילה את התיקון בהעתק ובודקת ב-Docker...")
            result = graph.invoke(Command(resume=answer), config)
    except LLMUnavailable as exc:
        error = str(exc)
        print(f"\n{exc}")
        return 1
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # אם משהו נכשל באמצע, finish לא רץ. מוחקים את ההעתק כאן
        values = graph.get_state(config).values
        work_dir = values.get("work_dir")
        if work_dir and Path(work_dir).exists():
            remove_sandbox(work_dir)
        print(f"הדוח המלא נשמר ב-{write_report(values, args.source, reviewer, error)}")

    skipped = result.get("skipped") or {}
    if skipped:
        print(f"\nדולגו {len(skipped)} קבצים (למשל: {next(iter(skipped.items()))})")
    if result.get("decision") == "approve" and result.get("diff") and not result.get("pushed"):
        Path(PATCH_FILE).write_text(result["diff"], encoding="utf-8")
        print(f"ה-diff נשמר ב-{PATCH_FILE}")
    print("\n" + result.get("result", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
