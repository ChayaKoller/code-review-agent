"""בדיקות לממשק: AgentSession (בלי Streamlit) והאפליקציה עצמה דרך AppTest של Streamlit.

גם כאן מודל ו-Docker מדומים, ו-origin הוא ריפו bare מקומי, כך שהדחיפה נבדקת באמת.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from core.docker_runner import DockerRunner
from core.llm import LLMUnavailable
from core.nodes import Deps
from test_graph import FakeReviewer, git, make_git_repo
from testkit import FakeDocker
from ui import session as session_module
from ui.flow import current_round, flow_states, heading_to
from ui.session import AgentSession

APP = str(Path(__file__).resolve().parents[1] / "streamlit_app.py")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for key in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(key, "test")
    for key in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(key, "test@example.com")


class CountingReviewer(FakeReviewer):
    """FakeReviewer עם המונים שהדוח קורא (calls, tokens)."""

    calls = input_tokens = output_tokens = 0


def fake_deps(**reviewer_kwargs) -> Deps:
    return Deps(reviewer=CountingReviewer(**reviewer_kwargs), runner=DockerRunner(image="test-image", run=FakeDocker()))


# ------------------------------------------------------------------ AgentSession


def test_session_stops_at_each_interrupt_and_pushes(tmp_path):
    repo, origin = make_git_repo(tmp_path)
    agent = AgentSession(fake_deps())
    seen: list[str] = []

    step = agent.start(str(repo), include=["app.py"], on_node=seen.append)
    assert step.kind == "review_fix"
    assert "+    start = (page - 1) * size" in step.info["diff"]
    assert seen[:3] == ["prepare", "select", "find_suspects"] and "screen_fixes" in seen

    step = agent.answer({"decision": "approve"})
    assert step.kind == "review_push" and step.info["verify_ok"]

    step = agent.answer({"decision": "push"})
    assert step.kind == "done" and step.values["pushed"] is True
    assert "(page - 1) * size" in git("show", f"{step.values['branch_name']}:app.py", cwd=origin)
    assert [h["decision"] for h in agent.history] == ["approve", "push"]
    assert not Path(step.values["work_dir"]).exists()


def test_session_reject_with_note_loops(tmp_path):
    repo, _ = make_git_repo(tmp_path)
    deps = fake_deps()
    agent = AgentSession(deps)
    agent.start(str(repo), include=["app.py"])
    step = agent.answer({"decision": "reject_with_note", "note": "קטן יותר"})
    assert step.kind == "review_fix" and step.info["attempt"] == 2
    assert deps.reviewer.notes[-1] == "קטן יותר"
    assert agent.answer({"decision": "reject"}).kind == "done"


def test_session_cannot_answer_when_not_waiting(tmp_path):
    repo, _ = make_git_repo(tmp_path, buggy=False)
    agent = AgentSession(fake_deps())
    assert agent.start(str(repo), include=["app.py"]).kind == "done"
    with pytest.raises(RuntimeError):
        agent.answer({"decision": "approve"})


def test_session_error_removes_the_copy(tmp_path):
    repo, _ = make_git_repo(tmp_path)

    class Down(CountingReviewer):
        def find_suspects(self, *args, **kwargs):
            raise LLMUnavailable("המודל לא זמין")

    agent = AgentSession(Deps(reviewer=Down(), runner=DockerRunner(image="test-image", run=FakeDocker())))
    step = agent.start(str(repo), include=["app.py"])
    assert step.kind == "error" and "לא זמין" in step.error
    assert not Path(step.values["work_dir"]).exists()


def test_cancel_while_waiting_removes_the_copy(tmp_path):
    repo, _ = make_git_repo(tmp_path)
    agent = AgentSession(fake_deps())
    step = agent.start(str(repo), include=["app.py"])
    work_dir = Path(step.values["work_dir"])
    assert work_dir.exists()  # הגרף מחכה ב-interrupt, וההעתק עוד קיים
    agent.cancel()
    assert not work_dir.exists()


# ------------------------------------------------------------------ האפליקציה


def _click(at: AppTest, label_part: str) -> AppTest:
    button = next(b for b in at.button if label_part in b.label)
    return button.click().run(timeout=60)


def test_app_full_flow(tmp_path, monkeypatch):
    repo, origin = make_git_repo(tmp_path)
    monkeypatch.setattr(session_module, "make_deps", lambda *a, **k: fake_deps())
    monkeypatch.chdir(tmp_path)  # הדוח נכתב לתיקייה הנוכחית

    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.text_input[0].input("app.py")  # include
    at.main.text_input[0].input(str(repo))
    at.main.button[0].click().run(timeout=60)  # התחל סקירה (כפתור הטופס)
    assert not at.exception
    assert "אישור ראשון" in at.title[0].value
    assert any("(page - 1) * size" in c.value for c in at.code)

    _click(at, "אשר")
    assert "אישור שני" in at.title[0].value

    _click(at, "דחוף")
    assert "תוצאה" in at.title[0].value
    assert at.success and "נדחף" in at.success[0].value
    assert (tmp_path / "cra_report.md").exists()

    _click(at, "סקירה חדשה")
    assert "סקירת קוד חדשה" in at.title[0].value


def test_app_reject_with_note_requires_text(tmp_path, monkeypatch):
    repo, _ = make_git_repo(tmp_path)
    monkeypatch.setattr(session_module, "make_deps", lambda *a, **k: fake_deps())
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.text_input[0].input("app.py")
    at.main.text_input[0].input(str(repo))
    at.main.button[0].click().run(timeout=60)

    _click(at, "דחה עם הערה")
    assert at.error and "כתבי הערה" in at.error[0].value  # בלי הערה לא ממשיכים

    at.text_area[0].input("קטן יותר")
    _click(at, "דחה עם הערה")
    assert any(m.value == "2 מתוך 2" for m in at.metric)


# ------------------------------------------------------------------ מפת התהליך


def _states(visited, phase, heading=None):
    return {n.node: n.state for n in flow_states(visited, phase, heading)}


def test_flow_marks_waiting_node_and_done_nodes():
    visited = ["prepare", "select", "find_suspects", "baseline", "propose_fix", "screen_fixes"]
    s = _states(visited, "review_fix")
    assert s["screen_fixes"] == "done" and s["review_fix"] == "waiting" and s["apply_fix"] == "pending"


def test_flow_running_follows_the_answer():
    visited = ["prepare", "select", "find_suspects", "baseline", "propose_fix", "screen_fixes", "review_fix"]
    assert _states(visited, "running", "propose_fix")["propose_fix"] == "running"  # דחייה עם הערה
    assert _states(visited, "running", "apply_fix")["apply_fix"] == "running"


def test_flow_after_end_marks_skipped_and_loop_count():
    visited = ["prepare", "select", "find_suspects", "baseline", "propose_fix", "screen_fixes", "review_fix",
               "propose_fix", "screen_fixes", "review_fix", "finish"]  # fmt: skip
    states = {n.node: n for n in flow_states(visited, "done")}
    assert states["publish"].state == "skipped" and states["finish"].state == "done"
    assert states["propose_fix"].runs == 2


def test_session_records_visited_nodes(tmp_path):
    repo, _ = make_git_repo(tmp_path)
    agent = AgentSession(fake_deps())
    agent.start(str(repo), include=["app.py"])
    assert agent.visited[-1] == "screen_fixes"
    agent.answer({"decision": "reject"})
    assert agent.visited[-2:] == ["review_fix", "finish"]


ROUND1 = ["prepare", "select", "find_suspects", "baseline", "propose_fix", "screen_fixes", "review_fix",
          "apply_fix", "verify_fix", "next_round"]  # fmt: skip


def test_flow_second_round_restarts_loop_nodes():
    s = _states(ROUND1 + ["find_suspects", "propose_fix", "screen_fixes"], "review_fix")
    assert s["review_fix"] == "waiting" and s["next_round"] == "done" and s["baseline"] == "done"
    assert s["apply_fix"] == "pending" and s["verify_fix"] == "pending"  # עוד לא רצו בסבב 2
    assert current_round(ROUND1) == 2


def test_flow_running_after_next_round_goes_to_find_then_propose():
    assert _states(ROUND1, "running")["find_suspects"] == "running"
    assert _states(ROUND1 + ["find_suspects"], "running")["propose_fix"] == "running"  # בלי baseline


def test_heading_after_reject_in_later_round_is_push_review():
    assert heading_to("reject", 1) == "finish"
    assert heading_to("reject", 2) == "review_push"
