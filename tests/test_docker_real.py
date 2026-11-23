"""בדיקות Docker אמיתי. מדלגות אוטומטית אם Docker לא רץ או שה-image לא נבנה.

הכנה (פעם אחת):  python -m scripts.docker_check
הרצה:            python -m pytest tests/test_docker_real.py -v

הבדיקה test_real_repo_libraries_are_inside_the_image צריכה רשת (pip בזמן הבנייה).
אם היא נכשלת בגלל תעודה (נטפרי), ראי את ההודעה: היא אומרת מה להגדיר ב-.env.
"""
import subprocess

import pytest

from core import config
from core.docker_runner import DockerError, DockerRunner
from testkit import make_repo

_runner = DockerRunner()


def _ready() -> bool:
    try:
        return _runner.is_available() and _runner.image_exists()
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _ready(), reason="Docker או ה-image לא מוכנים (python -m scripts.docker_check)"
)


def test_real_clean_repo_passes(tmp_path):
    result = _runner.run_tests(make_repo(tmp_path))
    assert result.passed, result.output


def test_real_buggy_repo_reports_the_failing_test_and_file(tmp_path):
    result = _runner.run_tests(make_repo(tmp_path, buggy=True))
    assert result.passed is False
    assert "tests/test_app.py::test_first_page" in result.failed
    assert result.failed_files == ["tests/test_app.py"]


def test_real_container_has_no_network(tmp_path):
    source = (
        "import socket\n"
        "import pytest\n\n"
        "def test_no_network():\n"
        "    with pytest.raises(OSError):\n"
        "        socket.create_connection(('1.1.1.1', 53), timeout=3)\n"
    )
    result = _runner.run_tests(make_repo(tmp_path, test_source=source))
    assert result.passed, "הקונטיינר הצליח להתחבר לרשת!\n" + result.output


def test_real_timeout_stops_the_container(tmp_path):
    slow = "import time\n\ndef test_slow():\n    time.sleep(60)\n"
    result = _runner.run_tests(make_repo(tmp_path, test_source=slow), timeout=5)
    assert result.timed_out
    ps = subprocess.run(
        ["docker", "ps", "-q", "--filter", f"name={config.DOCKER_CONTAINER_PREFIX}"],
        capture_output=True,
        text=True,
    )
    assert ps.stdout.strip() == "", "נשאר קונטיינר רץ אחרי timeout"


def test_real_compile_check(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "bad.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    result = _runner.check_compile(repo, ["app.py", "bad.py"])
    assert set(result.errors) == {"bad.py"}


def test_real_repo_libraries_are_inside_the_image(tmp_path):
    """הספרייה six מוזכרת ב-requirements של הריפו ולא קיימת ב-image הבסיס.
    ההרצה בלי רשת תעבור רק אם הספרייה נכנסה ל-image."""
    source = "import six\n\ndef test_six():\n    assert six.PY3\n"
    repo = make_repo(tmp_path, test_source=source)
    (repo / "requirements.txt").write_text("six==1.16.0\n")
    try:
        result = _runner.run_tests(repo)
    except DockerError as exc:
        pytest.fail(f"בניית ה-image עם הספריות נכשלה (רשת או נטפרי?):\n{exc}")
    assert result.passed, result.output
    assert not result.missing_modules
