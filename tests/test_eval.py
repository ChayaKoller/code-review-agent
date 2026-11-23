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
    assert fixed_bugs(BUGS, {"suspects": [], "fixes": []}) == []  # לא הוצע תיקון = 0, לא "לא נמדד"


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


# ------------------------------------------------------------- test set (באגים אמיתיים)


def test_test_dataset_is_valid():
    items = load_dataset(name="test")
    assert 8 <= len(items) <= 10
    assert len({x["repo"] for x in items}) >= 3
    for item in items:
        assert item["split"] == "test"
        assert {"buggy_commit", "fixed_commit", "bug_files", "python", "include", "extra_packages"} <= item.keys()
        for bug in item["bugs"]:
            assert {"id", "file", "line", "failing_test", "fix_commit"} <= bug.keys()
            assert bug["file"] in item["include"] and bug["file"] in item["bug_files"]


def test_fixed_matches_parametrized_tests_by_name():
    """pytest חותך שם של טסט עם פרמטרים ברווח, ולכן משווים לפי השם בלי הפרמטרים."""
    bugs = [{"id": "b", "file": "a.py", "line": 1, "failing_test": "tests/test_a.py::test_x"}]
    result = {"verification": "tests", "failing_tests": ["tests/test_a.py::test_x[pip"], "failed_after": [],
              "new_failures": [], "fixes": [{"file": "a.py"}]}  # fmt: skip
    assert fixed_bugs(bugs, result) == ["b"]
    assert fixed_bugs(bugs, {**result, "failed_after": ["tests/test_a.py::test_x[other"]}) == []


def _git(*args, cwd):
    import subprocess

    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd, check=True,
                   capture_output=True)  # fmt: skip


def test_prepare_snapshot_has_buggy_code_and_fixed_tests(tmp_path):
    from eval.snapshot import prepare_snapshot

    repo = tmp_path / "proj"
    (repo / "tests").mkdir(parents=True)
    (repo / "a.py").write_text("x = 1  # bug\n")
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "buggy", cwd=repo)
    (repo / "a.py").write_text("x = 2\n")
    (repo / "tests" / "test_a.py").write_text("def test_x(): pass\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-q", "-m", "fix", cwd=repo)
    import subprocess

    sha = lambda ref: subprocess.run(["git", "rev-parse", ref], cwd=repo, capture_output=True, text=True).stdout.strip()  # noqa: E731
    item = {"repo": str(repo), "buggy_commit": sha("HEAD~1"), "fixed_commit": sha("HEAD"), "bug_files": ["a.py"]}
    cache = tmp_path / "cache"
    cache.mkdir()
    _git("clone", "-q", str(repo), str(cache / "proj"), cwd=tmp_path)  # כמו cached_clone, בלי רשת
    snap = prepare_snapshot(item, tmp_path / "snap", cache_dir=cache)
    assert (snap / "a.py").read_text() == "x = 1  # bug\n"  # הקוד הבאגי
    assert (snap / "tests" / "test_a.py").exists()  # הטסט מ-commit התיקון
    status = subprocess.run(["git", "status", "--porcelain"], cwd=snap, capture_output=True, text=True).stdout
    assert status == ""  # הכול ב-commit: הסוכן מתחיל מעץ נקי
    remotes = subprocess.run(["git", "remote"], cwd=snap, capture_output=True, text=True).stdout
    assert remotes.strip() == ""  # אין origin: אי אפשר לדחוף מההעתק


def test_python_version_is_restored():
    from core import config
    from eval.run_eval import python_version

    old = config.DOCKER_PYTHON_VERSION
    with python_version("3.8"):
        assert config.DOCKER_PYTHON_VERSION == "3.8"
    assert config.DOCKER_PYTHON_VERSION == old
