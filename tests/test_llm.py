"""בדיקות ל-core/llm.py בלי רשת: מחליפים את המודל בשרשרת מדומה."""
from types import SimpleNamespace

from core.llm import Reviewer, _EditOut, _EditsOut, _SuspectOut, _SuspectsOut, number_lines


class FakeChain:
    def __init__(self, parsed):
        self.parsed, self.prompts = parsed, []

    def invoke(self, prompt):
        self.prompts.append(prompt)
        raw = SimpleNamespace(usage_metadata={"input_tokens": 10, "output_tokens": 2})
        return {"raw": raw, "parsed": self.parsed, "parsing_error": None}


def make_reviewer(find=None, fix=None):
    r = object.__new__(Reviewer)  # בלי __init__: בלי Gemini ובלי מפתח
    r._find, r._fix = FakeChain(find), FakeChain(fix)
    r.calls = r.input_tokens = r.output_tokens = 0
    return r


def test_number_lines():
    assert number_lines("a\nb") == "1: a\n2: b"


def test_find_suspects_drops_invented_lines_and_counts_tokens():
    out = _SuspectsOut(suspects=[_SuspectOut(line=2, bug_type="logic", reason="r", confidence=9),
                                 _SuspectOut(line=99, bug_type="logic", reason="r", confidence=3)])  # fmt: skip
    r = make_reviewer(find=out)
    result = r.find_suspects("a.py", "x = 1\ny = 2\n")
    # ביטחון מחוץ לטווח 1-5 נחתך
    assert result == [{"file": "a.py", "line": 2, "bug_type": "logic", "reason": "r", "confidence": 5}]
    assert (r.calls, r.input_tokens, r.output_tokens) == (1, 10, 2)
    assert "2: y = 2" in r._find.prompts[0]


def test_propose_fixes_passes_the_rejection_note():
    out = _EditsOut(edits=[_EditOut(start_line=1, end_line=1, new_code="x = 2", explanation="e")])
    r = make_reviewer(fix=out)
    suspects = [{"file": "a.py", "line": 1, "bug_type": "logic", "reason": "r"}]
    edits = r.propose_fixes("a.py", "x = 1\n", suspects, note="תיקון קטן")
    assert edits[0]["new_code"] == "x = 2" and edits[0]["file"] == "a.py"
    assert "תיקון קטן" in r._fix.prompts[0]


class FlakyChain(FakeChain):
    def __init__(self, parsed, failures, error="503 UNAVAILABLE"):
        super().__init__(parsed)
        self.failures, self.error = failures, error

    def invoke(self, prompt):
        if self.failures:
            self.failures -= 1
            raise RuntimeError(self.error)
        return super().invoke(prompt)


def test_busy_model_is_retried(monkeypatch):
    import core.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    r = make_reviewer()
    r._find = FlakyChain(_SuspectsOut(suspects=[]), failures=2)
    assert r.find_suspects("a.py", "x = 1\n") == []


def test_busy_model_gives_up_with_a_clear_error(monkeypatch):
    import pytest
    import core.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    r = make_reviewer()
    r._find = FlakyChain(None, failures=99)
    with pytest.raises(llm.LLMUnavailable, match="MODEL_NAME"):
        r.find_suspects("a.py", "x = 1\n")


def test_other_errors_are_not_retried():
    import pytest
    r = make_reviewer()
    r._find = FlakyChain(None, failures=1, error="400 INVALID_ARGUMENT")
    with pytest.raises(RuntimeError, match="400"):
        r.find_suspects("a.py", "x = 1\n")


def test_timeout_is_retried(monkeypatch):
    """קריאה שנתקעה ונעצרה ב-timeout נחשבת שגיאה זמנית: מנסים שוב ולא נופלים מיד."""
    import core.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    r = make_reviewer()
    r._find = FlakyChain(_SuspectsOut(suspects=[]), failures=1, error="ReadTimeout: The read operation timed out")
    assert r.find_suspects("a.py", "x = 1\n") == []


def test_disconnect_is_retried(monkeypatch):
    """ניתוק רשת בלי תשובה (נטפרי / השרת סגר חיבור) נחשב זמני: מנסים שוב, וההרצה לא נעצרת."""
    import core.llm as llm
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    r = make_reviewer()
    r._find = FlakyChain(_SuspectsOut(suspects=[]), failures=1, error="Server disconnected without sending a response.")
    assert r.find_suspects("a.py", "x = 1\n") == []
