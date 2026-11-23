"""בדיקות לגרף המלא, עם מודל מדומה ו-Docker מדומה (בלי רשת ובלי מכסה).

הריפו לדוגמה הוא ריפו git אמיתי עם "origin" שהוא ריפו bare מקומי, כך שאפשר
לבדוק clone, ענף, commit ו-push בלי GitHub.
מריצים מהשורש: python -m pytest tests -v
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from langgraph.types import Command

from core import config, git_ops
from core.docker_runner import DockerRunner
from core.graph import build_graph
from core.nodes import Deps, EditError, apply_edits
from core.publish import PublishError, push_branch
from core.sandbox import Sandbox
from testkit import CLEAN_SOURCE, FakeDocker, make_repo

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def git(*args, cwd):
    return subprocess.run(["git", *GIT_ID, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def make_git_repo(tmp_path: Path, buggy=True, with_tests=True) -> tuple[Path, Path]:
    """ריפו עם commit אחד ו-origin מקומי. מחזיר (ריפו, origin)."""
    repo = make_repo(tmp_path / "src", buggy=buggy, with_tests=with_tests)
    git("init", "-q", "-b", "main", cwd=repo)
    git("add", "-A", cwd=repo)
    git("commit", "-q", "-m", "init", cwd=repo)
    origin = tmp_path / "origin.git"
    git("clone", "-q", "--bare", str(repo), str(origin), cwd=tmp_path)
    git("remote", "add", "origin", str(origin), cwd=repo)
    return repo, origin


class FakeReviewer:
    """מודל מדומה: מוצא את באג ה-pagination ומתקן אותו."""

    def __init__(self, find=True, fix=True, confirm=True):
        self.find, self.fix, self.confirm = find, fix, confirm
        self.notes: list[str] = []
        self.failures: list[str] = []  # פלט הכשל שהגיע בניסיון השני (screen_fixes)
        self.checked: list[dict] = []

    def find_suspects(self, file, source):
        if not self.find or "page * size" not in source:
            return []
        line = source.splitlines().index("    start = page * size") + 1
        return [{"file": file, "line": line, "bug_type": "off_by_one", "reason": "page is 1-based", "confidence": 5}]

    def check_suspect(self, source, suspect):
        self.checked.append(suspect)
        return {"is_bug": self.confirm, "failing_input": "get_page([1,2,3,4], 1, 2)" if self.confirm else "",
                "expected": "[1, 2]", "actual": "[3, 4]"}  # fmt: skip

    def propose_fixes(self, file, source, suspects, note="", failure=""):
        self.notes.append(note)
        self.failures.append(failure)
        if not self.fix:
            return []
        s = suspects[0]
        return [{"file": file, "start_line": s["line"], "end_line": s["line"],
                 "new_code": "    start = (page - 1) * size", "explanation": "1-based page"}]  # fmt: skip


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    """commit בהעתק צריך שם ומייל. בבדיקות נותנים ערכים קבועים דרך משתני סביבה."""
    for key, value in {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
    }.items():  # fmt: skip
        monkeypatch.setenv(key, value)


def run_graph(source, reviewer, docker, answers, workspace=None):
    deps = Deps(reviewer=reviewer, runner=DockerRunner(image="test-image", run=docker), workspace_dir=workspace)
    graph = build_graph(deps)
    cfg = {"configurable": {"thread_id": "t"}}
    # app.py לדוגמה קצר מ-MIN_FILE_LINES, ולכן בוחרים אותו ידנית
    include = ["app.py"] if Path(source).is_dir() else []
    result = graph.invoke({"repo_path": str(source), "include": include}, cfg)
    interrupts = []
    while "__interrupt__" in result:
        info = result["__interrupt__"][0].value
        interrupts.append(info)
        result = graph.invoke(Command(resume=answers.pop(0)), cfg)
    return result, interrupts


# ------------------------------------------------------------- apply_edits


def test_apply_edits_replaces_lines_and_keeps_trailing_newline():
    src = "a\nb\nc\n"
    edits = [{"file": "f", "start_line": 2, "end_line": 2, "new_code": "B1\nB2", "explanation": ""}]
    assert apply_edits(src, edits) == "a\nB1\nB2\nc\n"


def test_apply_edits_rejects_bad_ranges_and_overlaps():
    src = "a\nb\nc\n"
    with pytest.raises(EditError):
        apply_edits(src, [{"file": "f", "start_line": 3, "end_line": 9, "new_code": "", "explanation": ""}])
    overlap = [
        {"file": "f", "start_line": 1, "end_line": 2, "new_code": "x", "explanation": ""},
        {"file": "f", "start_line": 2, "end_line": 3, "new_code": "y", "explanation": ""},
    ]
    with pytest.raises(EditError):
        apply_edits(src, overlap)


# ------------------------------------------------------------------ git


def test_is_git_url():
    assert git_ops.is_git_url("https://github.com/a/b")
    assert git_ops.is_git_url("git@github.com:a/b.git")
    assert not git_ops.is_git_url("C:\\repos\\b")
    assert git_ops.repo_name_from_url("https://github.com/a/my-repo.git") == "my-repo"
    assert git_ops.repo_name_from_url("git@github.com:a/b.git") == "b"


def test_sandbox_clones_from_url_and_original_is_untouched(tmp_path, monkeypatch):
    _, origin = make_git_repo(tmp_path)
    monkeypatch.setattr(config, "GIT_URL_PREFIXES", config.GIT_URL_PREFIXES + ("file://",))
    url = origin.resolve().as_uri()
    with Sandbox(url) as sb:
        assert sb.url == url and sb.is_git_repo and sb.has_tests
        assert (sb.path / "app.py").exists()
        work = sb.work_dir
    assert not work.exists()


def test_push_refuses_branch_that_is_not_the_agents(tmp_path):
    repo, _ = make_git_repo(tmp_path)
    with pytest.raises(PublishError, match="בטיחות"):
        push_branch(repo, "main")


# ------------------------------------------------------------------ הגרף


def test_full_flow_fix_verify_and_push(tmp_path, docker):
    repo, origin = make_git_repo(tmp_path)
    before = (repo / "app.py").read_text()

    result, interrupts = run_graph(repo, FakeReviewer(), docker, [{"decision": "approve"}, {"decision": "push"}])

    assert [i["kind"] for i in interrupts] == ["review_fix", "review_push"]
    assert "-    start = page * size" in interrupts[0]["diff"]
    assert "+    start = (page - 1) * size" in interrupts[0]["diff"]
    assert interrupts[1]["verification"] == "tests" and interrupts[1]["verify_ok"]
    assert result["pushed"] is True
    branch = result["branch_name"]
    assert branch.startswith(config.BRANCH_PREFIX)
    # הענף נדחף ל-origin עם התיקון, ו-main לא השתנה
    pushed = git("show", f"{branch}:app.py", cwd=origin)
    assert "(page - 1) * size" in pushed
    assert "page * size" in git("show", "main:app.py", cwd=origin)
    # המקור לא השתנה וההעתק נמחק
    assert (repo / "app.py").read_text() == before
    assert not Path(result["work_dir"]).exists()


def test_baseline_failures_are_not_blamed_on_the_fix(tmp_path, docker):
    repo, _ = make_git_repo(tmp_path)
    result, interrupts = run_graph(repo, FakeReviewer(), docker, [{"decision": "approve"}, {"decision": "no_push"}])
    assert result["failing_tests"]  # לפני התיקון test_second_page נכשל
    assert result["new_failures"] == []
    assert "לעבור" in result["verify_summary"]  # התיקון גרם לטסט שנכשל לעבור
    assert not result.get("pushed")


def test_reject_with_note_loops_back_to_the_model(tmp_path, docker):
    repo, _ = make_git_repo(tmp_path)
    reviewer = FakeReviewer()
    answers = [{"decision": "reject_with_note", "note": "קטן יותר"}, {"decision": "reject"}]
    result, interrupts = run_graph(repo, reviewer, docker, answers)
    assert [i["attempt"] for i in interrupts] == [1, 2]
    assert reviewer.notes == ["", "קטן יותר"]
    assert result["result"] == "התיקון נדחה. לא נכתב שום דבר."


def test_no_suspects_ends_without_asking(tmp_path, docker):
    repo, _ = make_git_repo(tmp_path, buggy=False)
    result, interrupts = run_graph(repo, FakeReviewer(), docker, [])
    assert interrupts == []
    assert result["result"] == "לא נמצאו חשדות לבאגים."
    assert not Path(result["work_dir"]).exists()


def test_repo_without_tests_is_checked_by_compile_only(tmp_path, docker):
    repo, _ = make_git_repo(tmp_path, with_tests=False)
    result, interrupts = run_graph(repo, FakeReviewer(), docker, [{"decision": "approve"}, {"decision": "no_push"}])
    assert interrupts[1]["verification"] == "compile_only"
    assert "לא נבדק בטסטים" in interrupts[1]["summary"]


def test_single_file_has_no_push_step(tmp_path, docker):
    f = tmp_path / "script.py"
    f.write_text(CLEAN_SOURCE.replace("(page - 1) * size", "page * size"), encoding="utf-8")
    before = f.read_text()
    result, interrupts = run_graph(f, FakeReviewer(), docker, [{"decision": "approve"}])
    assert [i["kind"] for i in interrupts] == ["review_fix"]
    assert result["verification"] == "compile_only"
    assert f.read_text() == before
