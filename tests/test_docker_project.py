"""בדיקות להתקנת ריפו שמוגדר כחבילה (pyproject.toml / setup.py) בתוך ה-image."""
import pytest

from core import config
from core.docker_runner import DockerRunner, find_project_files
from testkit import FakeDocker, make_repo


@pytest.fixture
def docker():
    return FakeDocker()


@pytest.fixture
def runner(docker):
    return DockerRunner(image="test-image", run=docker)


PYPROJECT = '[project]\nname = "myrepo"\nversion = "0.1"\ndependencies = ["six"]\n'


def test_pyproject_with_project_section_is_installed_with_test_extras(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text(PYPROJECT)
    assert find_project_files(repo) == ["pyproject.toml"]
    tag = runner.prepare_image(repo)
    assert tag.startswith(config.DOCKER_DEPS_IMAGE_REPO + ":")
    dockerfile = docker.build_contexts[0]["dockerfile"]
    assert "COPY src/ /tmp/src/" in dockerfile
    assert '"/tmp/src[test,tests,testing]"' in dockerfile
    assert "CRA_PROJECT_INSTALL_FAILED" in dockerfile  # כישלון לא עוצר את הבנייה


def test_pyproject_with_only_tool_settings_is_not_a_package(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    assert find_project_files(repo) == []
    assert runner.prepare_image(repo) == "test-image"


def test_changing_code_does_not_rebuild_the_image(tmp_path, runner, docker):
    repo = make_repo(tmp_path)
    (repo / "setup.py").write_text("from setuptools import setup\nsetup(name='x')\n")
    first = runner.prepare_image(repo)
    (repo / "app.py").write_text("# fixed\n")
    assert runner.prepare_image(repo) == first
    assert sum(1 for c in docker.calls if c[1] == "build") == 1


def test_install_project_can_be_turned_off(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    (repo / "setup.py").write_text("")
    monkeypatch.setattr(config, "DOCKER_INSTALL_PROJECT", False)
    assert find_project_files(repo) == []


def test_tests_run_with_the_copy_first_on_pythonpath(tmp_path, runner, docker):
    runner.run_tests(make_repo(tmp_path))
    run = next(c for c in docker.calls if c[1] == "run")
    assert "PYTHONPATH=/work/src:/work" in run
