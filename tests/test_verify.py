"""בדיקות ל-core/verify.py: יש טסטים מריצים אותם, אין בודקים קומפילציה בלבד."""
import pytest

from core import config
from core.docker_runner import DockerRunner
from core.sandbox import Sandbox
from core.state import VERIFY_COMPILE, VERIFY_TESTS
from core.verify import verify
from testkit import FakeDocker, make_repo


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture
def runner(docker):
    return DockerRunner(image=docker.image, run=docker)


def test_with_tests_that_pass(tmp_path, runner):
    with Sandbox(str(make_repo(tmp_path))) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_TESTS
    assert result.ok is True
    assert "עברו" in result.summary


def test_with_tests_that_fail(tmp_path, runner):
    with Sandbox(str(make_repo(tmp_path, buggy=True))) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_TESTS
    assert result.ok is False
    assert "tests/test_app.py" in result.summary


def test_without_tests_only_compiles_and_says_so(tmp_path, runner):
    with Sandbox(str(make_repo(tmp_path, with_tests=False))) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_COMPILE
    assert result.ok is True
    assert "לא נבדק בטסטים" in result.summary
    assert result.tests is None


def test_without_tests_syntax_error_is_reported_with_file_and_line(tmp_path, runner):
    repo = make_repo(tmp_path, with_tests=False)
    (repo / "app.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    with Sandbox(str(repo)) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_COMPILE
    assert result.ok is False
    assert "app.py" in result.summary
    assert "בשורה 1" in result.summary


def test_single_file_is_verified_by_compile(tmp_path, runner):
    f = tmp_path / "script.py"
    f.write_text("x = 1\n", encoding="utf-8")
    with Sandbox(str(f)) as sb:
        result = verify(sb, runner, ["script.py"])
    assert result.level == VERIFY_COMPILE
    assert result.ok is True


def test_missing_library_falls_back_to_compile_and_names_the_library(tmp_path, runner):
    source = "import nonexistent_pkg_xyz\n\ndef test_x():\n    pass\n"
    with Sandbox(str(make_repo(tmp_path, test_source=source))) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_COMPILE
    assert "nonexistent_pkg_xyz" in result.summary
    assert "לא רצו" in result.summary
    assert result.tests is not None and result.tests.missing_modules == ["nonexistent_pkg_xyz"]


def test_tests_folder_without_collected_tests_falls_back_to_compile(tmp_path, runner):
    with Sandbox(str(make_repo(tmp_path, test_source="# empty\n"))) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_COMPILE
    assert "לא מצא" in result.summary


def test_timeout_is_reported_as_a_tests_failure(tmp_path, runner, monkeypatch):
    monkeypatch.setattr(config, "TEST_TIMEOUT_SECONDS", 2)
    slow = "import time\n\ndef test_slow():\n    time.sleep(30)\n"
    with Sandbox(str(make_repo(tmp_path, test_source=slow))) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_TESTS
    assert result.ok is False
    assert "timeout" in result.summary


def test_pytest_that_cannot_start_is_reported(tmp_path, runner):
    repo = make_repo(tmp_path)
    (repo / "pytest.ini").write_text("[pytest]\naddopts = --no-such-option-xyz\n")
    with Sandbox(str(repo)) as sb:
        result = verify(sb, runner, ["app.py"])
    assert result.level == VERIFY_TESTS
    assert result.ok is False
    assert "קוד יציאה" in result.summary


def test_extra_packages_are_passed_on(tmp_path, runner, docker):
    with Sandbox(str(make_repo(tmp_path))) as sb:
        verify(sb, runner, ["app.py"], extra_packages=["six"])
    assert any(c[1] == "build" for c in docker.calls)


def test_no_files_to_compile_is_an_error(tmp_path, runner):
    with Sandbox(str(make_repo(tmp_path, with_tests=False))) as sb:
        with pytest.raises(ValueError):
            verify(sb, runner, [])
