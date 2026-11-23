"""בדיקה לדוח ההרצה של ה-CLI."""
from types import SimpleNamespace

from scripts.run_cli import write_report


def test_report_contains_suspects_diff_and_decisions(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    values = {
        "files": ["app.py"], "skipped": {"tests/test_app.py": "קובץ טסט"},
        "suspects": [{"file": "app.py", "line": 2, "bug_type": "off_by_one", "reason": "1-based"}],
        "fixes": [{"file": "app.py", "start_line": 2, "end_line": 2, "explanation": "fix"}],
        "diff": "-a\\n+b\\n", "has_tests": True, "failing_tests": ["t1"], "failed_after": [],
        "new_failures": [], "verification": "tests", "verify_summary": "כל הטסטים עברו.",
        "decision": "approve", "push_decision": "no_push", "result": "לא נדחף",
    }  # fmt: skip
    reviewer = SimpleNamespace(calls=2, input_tokens=100, output_tokens=20)
    text = write_report(values, "https://x/y", reviewer).read_text(encoding="utf-8")
    for part in ("app.py:2", "off_by_one", "```diff", "נכשלו לפני התיקון: 1", "approve", "no_push", "100"):
        assert part in text


def test_report_on_error_has_the_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reviewer = SimpleNamespace(calls=0, input_tokens=0, output_tokens=0)
    text = write_report({}, "repo", reviewer, error="503 UNAVAILABLE").read_text(encoding="utf-8")
    assert "503 UNAVAILABLE" in text and "## שגיאה" in text
