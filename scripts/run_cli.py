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

from core.graph import build_graph
from core.llm import Reviewer
from core.nodes import Deps
from core.sandbox import remove_sandbox

PATCH_FILE = "cra_fix.patch"


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

    deps = Deps(reviewer=Reviewer(), workspace_dir=args.workspace, extra_packages=args.extra_packages)
    graph = build_graph(deps)
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    state = {"repo_path": args.source, "include": args.include or [], "exclude": args.exclude or []}

    print("מכינה העתק, בוחרת קבצים ושולחת למודל...")
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
    finally:
        # אם משהו נכשל באמצע, finish לא רץ. מוחקים את ההעתק כאן
        work_dir = graph.get_state(config).values.get("work_dir")
        if work_dir and Path(work_dir).exists():
            remove_sandbox(work_dir)

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
