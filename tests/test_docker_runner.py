"""בדיקות ל-core/docker_runner.py עם Docker מדומה (ראי testkit.FakeDocker).

ה-Docker המדומה מריץ את הפקודה הפנימית באמת (pytest אמיתי), ולכן הפלט והניתוח שלו
אמיתיים. מה שלא נבדק כאן הוא Docker עצמו: את זה בודקים ב-test_docker_real.py.
"""
import pytest

from core import config
from core.docker_runner import DockerError, DockerRunner, file_of_test, find_requirement_files
from testkit import FakeDocker, make_repo


def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


def run_calls(docker):
    return [c for c in docker.calls if c[1] == "run"]


def build_calls(docker):
    return [c for c in docker.calls if c[1] == "build"]


def image_of(cmd):
    return cmd[cmd.index("/work") + 1]  # אחרי "-w /work" בא שם ה-image


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture
def runner(docker):
    return DockerRunner(image=docker.image, run=docker)


# ------------------------------------------------------------------ הרצת טסטים


def test_run_tests_passes_on_clean_repo(tmp_path, runner):
    result = runner.run_tests(make_repo(tmp_path))
    assert result.passed is True
    assert result.failed == []
    assert result.timed_out is False


def test_run_tests_reports_failing_test_and_file(tmp_path, runner):
    result = runner.run_tests(make_repo(tmp_path, buggy=True))
    assert result.passed is False
    assert "tests/test_app.py::test_first_page" in result.failed
    assert result.failed_files == ["tests/test_app.py"]


def test_container_is_locked_down_and_mounts_only_the_given_copy(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    runner.run_tests(repo)
    cmd = run_calls(docker)[0]
    assert "--rm" in cmd
    assert flag(cmd, "--network") == "none"
    assert flag(cmd, "--cap-drop") == "ALL"
    assert flag(cmd, "--security-opt") == "no-new-privileges"
    assert flag(cmd, "--memory") == config.DOCKER_MEMORY
    assert flag(cmd, "--pids-limit") == config.DOCKER_PIDS_LIMIT
    assert "--read-only" in cmd
    assert "PYTHONDONTWRITEBYTECODE=1" in cmd
    assert "no:cacheprovider" in cmd
    mounts = [a for a in cmd if a.startswith("type=bind")]
    assert mounts == [f"type=bind,source={repo.resolve()},target=/work"]


def test_timeout_stops_the_container(tmp_path, runner, docker):
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    result = runner.run_tests(make_repo(tmp_path, test_source=slow), timeout=2)
    assert result.timed_out is True
    assert result.passed is False
    run_cmd = run_calls(docker)[0]
    rm_cmds = [c for c in docker.calls if c[1] == "rm"]
    assert len(rm_cmds) == 1
    assert rm_cmds[0][:3] == ["docker", "rm", "-f"]
    assert rm_cmds[0][3] == flag(run_cmd, "--name")  # עוצרים בדיוק את הקונטיינר שהרצנו


def test_failure_to_stop_the_container_is_loud(tmp_path, runner, docker):
    docker.rm_rc = 1
    docker.rm_stderr = "boom"
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    with pytest.raises(DockerError, match="docker rm -f"):
        runner.run_tests(make_repo(tmp_path, test_source=slow), timeout=2)


def test_missing_library_is_reported(tmp_path, runner):
    source = "import nonexistent_pkg_xyz\n\ndef test_x():\n    pass\n"
    result = runner.run_tests(make_repo(tmp_path, test_source=source))
    assert result.passed is False
    assert result.missing_modules == ["nonexistent_pkg_xyz"]


def test_no_tests_collected_is_reported(tmp_path, runner):
    result = runner.run_tests(make_repo(tmp_path, test_source="# no tests here\n"))
    assert result.passed is False
    assert result.no_tests is True
    assert result.returncode == 5


# ----------------------------------------------------------------- שגיאות Docker


def test_is_available_follows_the_daemon(runner, docker):
    assert runner.is_available() is True
    docker.daemon_up = False
    assert runner.is_available() is False


def test_docker_not_installed(tmp_path):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("docker")

    runner = DockerRunner(image="x", run=boom)
    assert runner.is_available() is False
    with pytest.raises(DockerError, match="Docker"):
        runner.run_tests(make_repo(tmp_path))


def test_missing_image_gives_a_hint(tmp_path, runner, docker):
    docker.run_failure = (125, "Unable to find image 'test-image' locally")
    with pytest.raises(DockerError, match="docker_check"):
        runner.run_tests(make_repo(tmp_path))


def test_path_with_comma_is_rejected(tmp_path, runner):
    bad = tmp_path / "a,b"
    bad.mkdir()
    (bad / "app.py").write_text("x = 1\n")
    with pytest.raises(DockerError, match="פסיק"):
        runner.run_tests(bad)


def test_ensure_image_builds_the_base_when_missing(runner, docker):
    docker.images = set()
    runner.ensure_image()
    builds = build_calls(docker)
    assert len(builds) == 1
    assert f"PYTHON_VERSION={config.DOCKER_PYTHON_VERSION}" in builds[0]
    runner.ensure_image()  # עכשיו הוא קיים: לא בונים שוב
    assert len(build_calls(docker)) == 1


def test_ensure_image_requires_a_running_daemon(runner, docker):
    docker.daemon_up = False
    with pytest.raises(DockerError, match="Docker לא רץ"):
        runner.ensure_image()


def test_build_failure_shows_output_and_netfree_hint(runner, docker):
    docker.images = set()
    docker.build_rc = 1
    docker.build_output = "step 3\nSSL: CERTIFICATE_VERIFY_FAILED"
    with pytest.raises(DockerError, match="נטפרי") as err:
        runner.build_image()
    assert "CERTIFICATE_VERIFY_FAILED" in str(err.value)


# ------------------------------------------- הספריות של הריפו (image לכל ריפו)


def test_find_requirement_files(tmp_path):
    (tmp_path / "requirements.txt").write_text("six\n")
    (tmp_path / "requirements-dev.txt").write_text("pytest\n")
    (tmp_path / "docs-requirements.txt").write_text("sphinx\n")  # לא ברשימה
    assert find_requirement_files(tmp_path) == ["requirements.txt", "requirements-dev.txt"]


def test_no_requirements_uses_the_base_image(tmp_path, runner, docker):
    runner.run_tests(make_repo(tmp_path))
    assert build_calls(docker) == []
    assert image_of(run_calls(docker)[0]) == "test-image"


def test_requirements_are_built_into_an_image_once(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("six==1.16.0\n")

    tag = runner.prepare_image(repo)
    assert tag.startswith(config.DOCKER_DEPS_IMAGE_REPO + ":")
    assert runner.prepare_image(repo) == tag  # שוב: לא בונים מחדש
    assert len(build_calls(docker)) == 1

    ctx = docker.build_contexts[0]
    assert ctx["tag"] == tag
    assert "FROM test-image" in ctx["dockerfile"]
    assert "USER root" in ctx["dockerfile"]
    assert "-r requirements.txt" in ctx["dockerfile"]
    assert ctx["files"] == ["requirements.txt"]

    runner.run_tests(repo)
    assert image_of(run_calls(docker)[0]) == tag  # הטסטים רצים ב-image עם הספריות
    assert len(build_calls(docker)) == 1


def test_changed_requirements_get_a_new_image(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("six==1.16.0\n")
    first = runner.prepare_image(repo)
    (repo / "requirements.txt").write_text("six==1.15.0\n")
    second = runner.prepare_image(repo)
    assert first != second
    assert len(build_calls(docker)) == 2


def test_several_requirement_files_are_installed_together(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("six\n")
    (repo / "requirements-dev.txt").write_text("-r requirements.txt\niniconfig\n")
    runner.prepare_image(repo)
    ctx = docker.build_contexts[0]
    assert ctx["files"] == ["requirements-dev.txt", "requirements.txt"]
    assert "-r requirements.txt -r requirements-dev.txt" in ctx["dockerfile"]


def test_extra_packages_are_added_to_the_image(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    tag = runner.prepare_image(repo, extra_packages=["numpy", " pandas ", ""])
    assert tag.startswith(config.DOCKER_DEPS_IMAGE_REPO + ":")
    ctx = docker.build_contexts[0]
    assert ctx["files"] == ["cra_extra_requirements.txt"]
    assert "-r cra_extra_requirements.txt" in ctx["dockerfile"]


def test_extra_packages_cannot_inject_pip_options(tmp_path, runner):
    repo = make_repo(tmp_path)
    with pytest.raises(ValueError):
        runner.prepare_image(repo, extra_packages=["--index-url http://evil.example"])
    with pytest.raises(ValueError):
        runner.prepare_image(repo, extra_packages=["-r /etc/passwd"])


def test_deps_build_failure_explains_unsupported_lines(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("-e .\n")
    docker.build_rc = 1
    docker.build_output = "ERROR: Could not open requirements file: No such file or directory: 'base.txt'"
    with pytest.raises(DockerError, match="לא נתמכות"):
        runner.prepare_image(repo)


# ----------------------------------------------------------------- קומפילציה


def test_compile_check_reports_syntax_errors_only_for_bad_files(tmp_path, runner):
    repo = make_repo(tmp_path)
    (repo / "good.py").write_text("# שלום\nx = 1\n", encoding="utf-8")
    (repo / "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    result = runner.check_compile(repo, ["app.py", "good.py", "bad.py"])
    assert result.ok is False
    assert set(result.errors) == {"bad.py"}
    assert result.errors["bad.py"]["line"] == 1


def test_compile_check_ok(tmp_path, runner):
    result = runner.check_compile(make_repo(tmp_path), ["app.py"])
    assert result.ok is True
    assert result.errors == {}


def test_compile_check_writes_nothing_into_the_copy(tmp_path, runner):
    repo = make_repo(tmp_path)
    before = sorted(p.name for p in repo.rglob("*"))
    runner.check_compile(repo, ["app.py"])
    assert sorted(p.name for p in repo.rglob("*")) == before


def test_compile_check_with_no_files_does_not_call_docker(tmp_path, runner, docker):
    assert runner.check_compile(make_repo(tmp_path), []).ok is True
    assert docker.calls == []


def test_compile_check_uses_the_base_image_even_with_requirements(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "requirements.txt").write_text("six\n")
    runner.check_compile(repo, ["app.py"])
    assert build_calls(docker) == []
    assert image_of(run_calls(docker)[0]) == "test-image"


def test_file_of_test():
    assert file_of_test("tests/test_a.py::test_x") == "tests/test_a.py"
    assert file_of_test("tests/test_a.py") == "tests/test_a.py"
