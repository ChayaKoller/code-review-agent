"""tests/testkit.py: כלי עזר משותפים לבדיקות: ריפו לדוגמה ו-Docker מדומה."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CLEAN_SOURCE = '''\
def get_page(items, page, size):
    start = (page - 1) * size
    return items[start:start + size]
'''

BUGGY_SOURCE = CLEAN_SOURCE.replace("(page - 1) * size", "page * size")

TEST_SOURCE = '''\
from app import get_page


def test_first_page():
    assert get_page([1, 2, 3, 4], 1, 2) == [1, 2]


def test_second_page():
    assert get_page([1, 2, 3, 4], 2, 2) == [3, 4]
'''


def make_repo(
    tmp_path: Path,
    buggy: bool = False,
    test_source: str = TEST_SOURCE,
    with_tests: bool = True,
) -> Path:
    repo = tmp_path / "myrepo"
    repo.mkdir(parents=True)
    (repo / "app.py").write_text(BUGGY_SOURCE if buggy else CLEAN_SOURCE, encoding="utf-8")
    if with_tests:
        (repo / "tests").mkdir()
        (repo / "tests" / "test_app.py").write_text(test_source, encoding="utf-8")
    return repo


def _completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


class FakeDocker:
    """מחליף את subprocess.run: מדמה את הפקודה docker.

    פקודות כמו version, image inspect, build ו-rm מקבלות תשובה מדומה (ואפשר לשלוט בה).
    את docker run הוא מריץ באמת: לוקח את הפקודה הפנימית (python -m pytest ...) ומריץ
    אותה מקומית בתיקייה שהייתה מחוברת ל-/work. כך הבדיקות רואות פלט אמיתי של pytest.
    """

    def __init__(self, image: str = "test-image"):
        self.image = image
        self.images = {image}  # images שקיימים
        self.daemon_up = True
        self.build_rc = 0
        self.build_output = ""
        self.rm_rc = 0
        self.rm_stderr = ""
        self.run_failure: tuple[int, str] | None = None  # (קוד, stderr) לכל docker run
        self.calls: list[list[str]] = []
        self.build_contexts: list[dict] = []  # מה היה בהקשר של כל בנייה

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        assert cmd[0] == "docker", cmd
        verb = cmd[1]
        if verb == "version":
            return _completed(cmd, 0 if self.daemon_up else 1, "27.0\n" if self.daemon_up else "")
        if verb == "image":  # docker image inspect <image>
            return _completed(cmd, 0 if cmd[3] in self.images else 1)
        if verb == "build":
            return self._build(cmd)
        if verb == "rm":
            return _completed(cmd, self.rm_rc, stderr=self.rm_stderr)
        if verb == "run":
            return self._run_container(cmd, kwargs)
        raise AssertionError(f"פקודת docker לא צפויה: {cmd}")

    def _build(self, cmd):
        tag = cmd[cmd.index("-t") + 1]
        ctx = Path(cmd[-1])
        dockerfile = Path(cmd[cmd.index("-f") + 1])
        reqs = ctx / "reqs"
        self.build_contexts.append(
            {
                "tag": tag,
                "dockerfile": dockerfile.read_text(encoding="utf-8"),
                "files": sorted(p.name for p in reqs.iterdir()) if reqs.is_dir() else [],
            }
        )
        if self.build_rc == 0:
            self.images.add(tag)
            return _completed(cmd, 0)
        return _completed(cmd, self.build_rc, stderr=self.build_output)

    def _run_container(self, cmd, kwargs):
        if self.run_failure:
            rc, err = self.run_failure
            return _completed(cmd, rc, stderr=err)
        mount = next(a for a in cmd if a.startswith("type=bind"))
        source = dict(part.split("=", 1) for part in mount.split(","))["source"]
        inner = cmd[cmd.index("/work") + 2 :]  # אחרי "-w /work" בא ה-image, ואחריו הפקודה
        if inner[0] == "python":
            inner[0] = sys.executable
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(inner, cwd=source, env=env, **kwargs)
