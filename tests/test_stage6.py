"""בדיקות לשלב 6: קטעי קוד קצרים למודל, סינון חשדות, מאמת, ובדיקת כל תיקון לבד (screen_fixes)."""
from __future__ import annotations

from pathlib import Path

from core import config
from core.code_context import enclosing_span, excerpt
from core.filtering import dedupe, rank, split_by_confidence
from core.llm import _VerdictOut
from core.nodes import failure_excerpt
from test_graph import FakeReviewer, run_graph
from test_llm import FakeChain, make_reviewer
from testkit import BUGGY_SOURCE, FakeDocker, TEST_SOURCE

TWO_FUNCS = '''import re


def a(x):
    y = x + 1
    return y


def b(s):
    return re.findall("x", s)[0]
'''


# ------------------------------------------------------------ code_context


def test_enclosing_span_finds_the_function():
    assert enclosing_span(TWO_FUNCS, 5) == (4, 6)
    assert enclosing_span(TWO_FUNCS, 10) == (9, 10)


def test_excerpt_sends_only_the_function_and_imports_with_original_numbers():
    text = excerpt(TWO_FUNCS, [10])
    assert "1: import re" in text and "10:     return re.findall" in text
    assert "def a" not in text  # הפונקציה השנייה לא נשלחת


def test_excerpt_on_broken_file_falls_back_to_a_window():
    assert "3: c" in excerpt("a\nb\nc\nd(\n", [3])


# --------------------------------------------------------------- filtering


def sus(file, line, conf):
    return {"file": file, "line": line, "bug_type": "logic", "reason": "", "confidence": conf}


def test_dedupe_keeps_the_most_confident_of_close_suspects():
    out = dedupe([sus("a.py", 16, 3), sus("a.py", 17, 5), sus("b.py", 17, 2), sus("a.py", 30, 1)])
    assert [(s["file"], s["line"]) for s in out] == [("a.py", 17), ("b.py", 17), ("a.py", 30)]


def test_rank_and_threshold():
    above, below = split_by_confidence([sus("a", 1, 2), sus("b", 1, 4), sus("c", 1, 3)], 3)
    assert [s["file"] for s in rank(above)] == ["b", "c"] and [s["file"] for s in below] == ["a"]


# --------------------------------------------------------------- המאמת


def test_check_suspect_requires_a_concrete_input_and_sends_only_the_function():
    r = make_reviewer()
    r._check = FakeChain(_VerdictOut(failing_input="", expected="", actual="", is_bug=True))
    s = sus("f.py", 10, 5)
    assert r.check_suspect(TWO_FUNCS, s)["is_bug"] is False  # בלי קלט מפיל: לא באג
    assert "def a" not in r._check.prompts[0]
    r._check = FakeChain(_VerdictOut(failing_input="b('y')", expected="error msg", actual="IndexError", is_bug=True))
    assert r.check_suspect(TWO_FUNCS, s)["is_bug"] is True


def test_rejected_suspect_never_reaches_the_fix_without_tests(tmp_path):
    from testkit import make_repo

    repo = make_repo(tmp_path, buggy=True, with_tests=False)  # בלי טסטים אין מי "שיציל" חשד שנזרק
    reviewer = FakeReviewer(confirm=False)
    result, interrupts = run_graph(repo, reviewer, FakeDocker(), [])
    assert interrupts == [] and result["suspects"] == [] and reviewer.notes == []
    assert result["rejected_suspects"][0]["rejected_because"].startswith("המאמת")
    assert "נזרקו בסינון" in result["result"]


# ------------------------------------------------------------ screen_fixes

OTHER = '''def double(x):
    return x * 2
'''
OTHER_TEST = '''from other import double


def test_double():
    assert double(2) == 4
'''


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "r"
    (repo / "tests").mkdir(parents=True)
    (repo / "app.py").write_text(BUGGY_SOURCE, encoding="utf-8")
    (repo / "other.py").write_text(OTHER, encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(TEST_SOURCE, encoding="utf-8")
    (repo / "tests" / "test_other.py").write_text(OTHER_TEST, encoding="utf-8")
    return repo


class TwoFileReviewer(FakeReviewer):
    """מוצא את הבאג האמיתי ב-app.py, וגם "באג" שגוי ב-other.py שהתיקון שלו שובר טסט."""

    def find_suspects(self, file, source):
        if file == "other.py":
            return [{"file": file, "line": 2, "bug_type": "logic", "reason": "should add", "confidence": 4}]
        return super().find_suspects(file, source)

    def propose_fixes(self, file, source, suspects, note="", failure=""):
        if file == "other.py":
            self.failures.append(failure)
            return [{"file": file, "start_line": 2, "end_line": 2, "new_code": "    return x + 3",
                     "explanation": "wrong"}]  # fmt: skip
        return super().propose_fixes(file, source, suspects, note, failure)


def run_two(tmp_path, reviewer):
    repo = _repo(tmp_path)
    from core.docker_runner import DockerRunner
    from core.graph import build_graph
    from core.nodes import Deps
    from langgraph.types import Command

    graph = build_graph(Deps(reviewer=reviewer, runner=DockerRunner(image="test-image", run=FakeDocker())))
    cfg = {"configurable": {"thread_id": "t"}}
    state = graph.invoke({"repo_path": str(repo), "include": ["app.py", "other.py"]}, cfg)
    first = state["__interrupt__"][0].value
    state = graph.invoke(Command(resume={"decision": "approve"}), cfg)
    return first, state


def test_breaking_fix_is_retried_with_the_failure_and_then_dropped(tmp_path):
    reviewer = TwoFileReviewer()
    first, state = run_two(tmp_path, reviewer)
    by_file = {r["file"]: r for r in first["screening"]}
    assert by_file["app.py"]["status"] == "tests_confirmed"
    assert by_file["other.py"]["status"] == "broke_tests" and by_file["other.py"]["attempts"] == 1 + config.FIX_RETRIES
    assert "test_double" in reviewer.failures[-1]  # הניסיון השני קיבל את פלט הכשל
    assert {e["file"] for e in first["fixes"]} == {"app.py"}  # רק התיקון שאושר מגיע לאדם
    assert [s["file"] for s in first["suspects"]] == ["app.py"]
    assert first["suspects"][0]["evidence"] == "tests_confirmed"
    assert state["verify_ok"] and state["new_failures"] == []


def test_failure_excerpt_starts_at_the_test_header():
    out = "junk\n____ test_double ____\nassert 5 == 4\n" + "x" * 50
    assert failure_excerpt(out, ["tests/t.py::test_double"], limit=30).startswith("____ test_double")
    assert failure_excerpt("abc", ["t::nothing"], limit=2) == "bc"


def test_load_dataset_by_ids_and_quick_ids_exist():
    from eval.run_eval import QUICK_IDS, load_dataset

    items = load_dataset(name="test", ids=QUICK_IDS["test"])
    assert sorted(x["id"] for x in items) == sorted(QUICK_IDS["test"])
    assert [x["id"] for x in load_dataset(name="test", ids=["thefuck_1"])] == ["thefuck_1"]


class SecondTryReviewer(FakeReviewer):
    """הניסיון הראשון לא מתקן (אבל גם לא שובר); השני, אחרי שראה את פלט הטסט, מתקן."""

    def propose_fixes(self, file, source, suspects, note="", failure=""):
        self.failures.append(failure)
        s = suspects[0]
        code = "    start = (page - 1) * size" if failure else "    start = page * size  # same bug"
        return [{"file": file, "start_line": s["line"], "end_line": s["line"], "new_code": code,
                 "explanation": "x"}]  # fmt: skip


def test_unproven_fix_is_retried_with_the_still_failing_related_test(tmp_path):
    from core.nodes import related_tests

    assert related_tests("pkg/rules/mkdir_p.py", ["tests/rules/test_mkdir_p.py::t[a]", "tests/test_x.py::t"]) == [
        "tests/rules/test_mkdir_p.py::t[a]"
    ]
    reviewer = SecondTryReviewer()
    result, interrupts = run_graph(_repo(tmp_path), reviewer, FakeDocker(), [{"decision": "approve"}])
    first = interrupts[0]
    app = next(r for r in first["screening"] if r["file"] == "app.py")
    assert app["status"] == "tests_confirmed" and app["attempts"] == 2
    assert "did not make this failing test pass" in reviewer.failures[-1]
    assert "same bug" in reviewer.failures[-1]  # המודל רואה את התיקון הקודם שלו
    assert "(page - 1)" in first["diff"]


def test_checker_rejected_suspect_survives_only_if_tests_confirm(tmp_path):
    # המאמת זורק את הבאג האמיתי, אבל התיקון מתקן טסט שנכשל: החשד חוזר עם ראיה מהטסטים
    result, interrupts = run_graph(_repo(tmp_path), FakeReviewer(confirm=False), FakeDocker(), [{"decision": "approve"}])
    first = interrupts[0]
    assert first["suspects"][0]["evidence"] == "tests_confirmed"
    assert not any(r["file"] == "app.py" for r in result["rejected_suspects"])


def test_checker_rejected_suspect_is_dropped_when_fix_proves_nothing(tmp_path):
    reviewer = TwoFileReviewer(confirm=False)  # other.py: התיקון שובר; app.py: מתקן
    first, _ = run_two(tmp_path, reviewer)
    assert {e["file"] for e in first["fixes"]} == {"app.py"}


def test_langsmith_replay_uses_saved_results_without_running(tmp_path, monkeypatch):
    import json as _json
    import sys
    import types

    from eval import run_eval

    saved = tmp_path / "before.json"
    saved.write_text(_json.dumps([{"id": "thefuck_1", "result": {"suspects": []}}]), encoding="utf-8")
    calls = {}

    class Ex:
        def __init__(self, i):
            self.inputs = {"id": i}

    class Client:
        def has_dataset(self, dataset_name):
            return True

        def list_examples(self, dataset_name):
            return [Ex("thefuck_1"), Ex("thefuck_2")]

    def evaluate(target, data, **kw):
        calls["data"] = [e.inputs["id"] for e in data]
        calls["out"] = target({"id": "thefuck_1"})

    monkeypatch.setitem(sys.modules, "langsmith", types.SimpleNamespace(Client=Client, evaluate=evaluate))
    run_eval.run_langsmith("before", "test", str(saved))
    assert calls == {"data": ["thefuck_1"], "out": {"suspects": []}}
