"""בדיקות להערכה: המדדים, וריצה מלאה בלי עצירות עם מודל ו-Docker מדומים."""
from core.docker_runner import DockerRunner
from eval.metrics import detection, fixed_bugs, score
from eval.run_eval import load_dataset, run_agent
from test_graph import FakeReviewer, make_git_repo
from testkit import FakeDocker

BUGS = [
    {"id": "b1", "file": "a.py", "line": 10, "failing_test": "tests/test_a.py::t1"},
    {"id": "b2", "file": "b.py", "line": 5, "failing_test": "tests/test_b.py::t2"},
]


def s(file, line):
    return {"file": file, "line": line, "bug_type": "logic", "reason": ""}


def test_detection_near_and_exact():
    suspects = [s("a.py", 12), s("c.py", 1)]
    near = detection(suspects, BUGS, 3)
    assert near["found"] == ["b1"] and near["recall"] == 0.5 and near["precision"] == 0.5
    assert detection(suspects, BUGS, 0)["recall"] == 0.0
    assert detection([], BUGS, 3)["precision"] is None  # אין חשדות: precision לא מוגדר


def test_fixed_requires_failing_before_passing_after_and_nothing_broken():
    result = {"verification": "tests", "failing_tests": ["tests/test_a.py::t1", "tests/test_b.py::t2"],
              "failed_after": ["tests/test_b.py::t2"], "new_failures": [], "fixes": [{"file": "a.py"}]}  # fmt: skip
    assert fixed_bugs(BUGS, result) == ["b1"]
    assert fixed_bugs(BUGS, {**result, "new_failures": ["tests/test_c.py::x"]}) == []
    assert fixed_bugs(BUGS, {**result, "fixes": [{"file": "tests/test_a.py"}]}) == []
    assert fixed_bugs(BUGS, {**result, "verification": "compile_only"}) is None


def test_dev_dataset_is_valid():
    items = load_dataset()
    assert {x["id"] for x in items} == {"library_tracker", "order_reports"}
    for item in items:
        for bug in item["bugs"]:
            assert {"id", "file", "line", "failing_test"} <= bug.keys()


def test_run_agent_end_to_end_never_pushes(tmp_path):
    repo, origin = make_git_repo(tmp_path)
    (repo / "app.py").write_text((repo / "app.py").read_text() + "\n\n\n# padding\n")  # מעל MIN_FILE_LINES
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "pad"], cwd=repo, check=True)
    result = run_agent(str(repo), reviewer=FakeReviewer(), runner=DockerRunner(image="test-image", run=FakeDocker()))
    bugs = [{"id": "x", "file": "app.py", "line": 2, "failing_test": "tests/test_app.py::test_second_page"}]
    m = score(result, bugs)
    assert m["recall"] == 1.0 and m["precision"] == 1.0 and m["fix_rate"] == 1.0
    branches = subprocess.run(["git", "branch"], cwd=origin, capture_output=True, text=True).stdout
    assert "cra/fix-" not in branches
