"""v13: סבבים. אחרי תיקון שאושר, הסוכן חוזר לאיתור על הקוד המתוקן, עד שאין מה לתקן."""
from __future__ import annotations

import pytest

from core import config
from core.graph import should_continue
from test_graph import FakeReviewer, _git_identity, docker, git, make_git_repo, run_graph  # noqa: F401 (fixtures)

TOTAL_BUG = "def total(items):\n    return sum(items) - 1\n"
TOTAL_TEST = "\n\nfrom app import total\n\n\ndef test_total():\n    assert total([1, 2]) == 3\n"


class TwoBugReviewer(FakeReviewer):
    """מוצא באג אחד בכל קריאה: קודם את ה-pagination, ורק כשהוא מתוקן את total.
    כך סבב 1 מתקן באג אחד, וסבב 2 צריך לראות את הקוד המתוקן כדי למצוא את השני."""

    def __init__(self, find_total=True):
        super().__init__()
        self.find_total = find_total
        self.rounds_seen: list[str] = []

    def find_suspects(self, file, source, context=""):
        if "page * size" in source and "(page - 1)" not in source:
            self.rounds_seen.append("page")
            return super().find_suspects(file, source, context)
        if self.find_total and "sum(items) - 1" in source:
            self.rounds_seen.append("total")
            line = source.splitlines().index("    return sum(items) - 1") + 1
            return [{"file": file, "line": line, "bug_type": "logic", "reason": "off by one", "confidence": 5}]
        return []

    def propose_fixes(self, file, source, suspects, note="", failure=""):
        s = suspects[0]
        if s["reason"] == "off by one":
            return [{"file": file, "start_line": s["line"], "end_line": s["line"],
                     "new_code": "    return sum(items)", "explanation": "no -1"}]  # fmt: skip
        return super().propose_fixes(file, source, suspects, note, failure)


@pytest.fixture
def two_bug_repo(tmp_path):
    repo, origin = make_git_repo(tmp_path)
    (repo / "app.py").write_text((repo / "app.py").read_text() + "\n\n" + TOTAL_BUG, encoding="utf-8")
    test_file = repo / "tests" / "test_app.py"
    test_file.write_text(test_file.read_text() + TOTAL_TEST, encoding="utf-8")
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "second bug", cwd=repo)
    git("push", "-q", "origin", "main", cwd=repo)
    return repo, origin


def test_two_rounds_fix_both_bugs_and_push_once(two_bug_repo, docker):
    repo, origin = two_bug_repo
    reviewer = TwoBugReviewer()
    answers = [{"decision": "approve"}, {"decision": "approve"}, {"decision": "push"}]
    result, interrupts = run_graph(repo, reviewer, docker, answers)

    assert [i["kind"] for i in interrupts] == ["review_fix", "review_fix", "review_push"]
    assert [i["round"] for i in interrupts[:2]] == [1, 2]
    assert reviewer.rounds_seen == ["page", "total"]  # סבב 2 ראה את הקוד המתוקן
    push = interrupts[2]
    assert "(page - 1) * size" in push["diff"] and "return sum(items)" in push["diff"]  # ה-diff המצטבר
    assert [r["round"] for r in push["rounds"]] == [1]
    assert result["failed_after"] == []
    assert len(result["suspects"]) == 2 and len(result["fixes"]) == 2  # מאוחד לדוח ולהערכה
    branch_app = git("show", f"{result['branch_name']}:app.py", cwd=origin)
    assert "(page - 1) * size" in branch_app and "return sum(items)\n" in branch_app


def test_max_rounds_one_behaves_like_before(two_bug_repo, docker, monkeypatch):
    monkeypatch.setattr(config, "MAX_ROUNDS", 1)
    repo, _ = two_bug_repo
    result, interrupts = run_graph(repo, TwoBugReviewer(), docker, [{"decision": "approve"}, {"decision": "no_push"}])
    assert [i["kind"] for i in interrupts] == ["review_fix", "review_push"]
    assert result["failed_after"] == ["tests/test_app.py::test_total"]


def test_second_round_without_suspects_still_offers_push(two_bug_repo, docker):
    repo, _ = two_bug_repo
    reviewer = TwoBugReviewer(find_total=False)  # בסבב 2 לא נמצא כלום
    result, interrupts = run_graph(repo, reviewer, docker, [{"decision": "approve"}, {"decision": "no_push"}])
    assert [i["kind"] for i in interrupts] == ["review_fix", "review_push"]
    assert "(page - 1) * size" in interrupts[1]["diff"]
    assert "2 סבבים" in result["result"]


def test_rejecting_round_two_keeps_round_one(two_bug_repo, docker):
    repo, _ = two_bug_repo
    answers = [{"decision": "approve"}, {"decision": "reject"}, {"decision": "no_push"}]
    result, interrupts = run_graph(repo, TwoBugReviewer(), docker, answers)
    assert [i["kind"] for i in interrupts] == ["review_fix", "review_fix", "review_push"]
    assert "return sum(items)" not in interrupts[2]["diff"]  # התיקון שנדחה לא נכנס
    assert len(result["fixes"]) == 1


def _state(**kw):
    base = {"has_tests": True, "verification": "tests", "failing_tests": ["a", "b"], "failed_after": ["b"],
            "new_failures": [], "round": 1}  # fmt: skip
    return {**base, **kw}


def test_stop_conditions():
    assert should_continue(_state())
    assert not should_continue(_state(failed_after=[]))  # אין מה לתקן
    assert not should_continue(_state(failed_after=["a", "b"]))  # הסבב לא שיפר
    assert not should_continue(_state(new_failures=["c"]))  # נשבר משהו
    assert not should_continue(_state(round=config.MAX_ROUNDS))  # הגענו לתקרה
    assert not should_continue(_state(verification="compile_only"))  # אין טסטים: סבב אחד
    assert not should_continue(_state(baseline_broken=True))
