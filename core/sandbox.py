"""core/sandbox.py: יצירה וניהול של העתק עבודה זמני.

הכלל: כל כתיבה קורית רק בהעתק. המקור לא משתנה.
הקובץ הזה לא מריץ קוד של הפרויקט הנבדק. ההרצה היא תפקידו של docker_runner.
"""
from __future__ import annotations

import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from core import config


class SandboxError(ValueError):
    """הקלט לא מתאים: הנתיב לא קיים, אין בו פייתון, או שתיקיית האב לא תקינה."""


# ---------------------------------------------------------------- בדיקת קלט


def _walk_py(root: Path, pattern: str):
    """מחזיר קבצים לפי תבנית, בלי תיקיות שמתעלמים מהן."""
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in config.COPY_IGNORE_DIRS and d != ".git"]
        for f in files:
            if Path(f).match(pattern):
                yield Path(dirpath) / f


def _has_tests(root: Path) -> bool:
    for name in config.TEST_DIR_NAMES:
        if (root / name).is_dir():
            return True
    for pattern in config.TEST_FILE_PATTERNS:
        if pattern == "conftest.py":
            continue  # conftest לבד לא אומר שיש טסטים
        if any(_walk_py(root, pattern)):
            return True
    return False


def validate_source(source_path: str) -> Path:
    """המקור יכול להיות ריפו (תיקייה עם קבצי פייתון) או קובץ .py בודד.
    טסטים לא נדרשים: בלעדיהם התיקון ייבדק בקומפילציה בלבד (ראי verify.py)."""
    src = Path(source_path).expanduser().resolve()
    if not src.exists():
        raise SandboxError(f"הנתיב לא קיים: {src}")
    if src.is_file():
        if src.suffix != ".py":
            raise SandboxError(f"קובץ בודד חייב להיות .py: {src.name}")
        return src
    if not src.is_dir():
        raise SandboxError(f"הנתיב אינו קובץ או תיקייה: {src}")
    if not any(_walk_py(src, "*.py")):
        raise SandboxError("לא נמצאו קבצי פייתון בתיקייה")
    return src


def _validate_workspace(workspace_dir: str, source: Path) -> Path:
    ws = Path(workspace_dir).expanduser().resolve()
    if not ws.is_dir():
        raise SandboxError(f"תיקיית האב לא קיימת (צריך ליצור אותה קודם): {ws}")
    if source.is_dir() and (ws == source or source in ws.parents):
        raise SandboxError("תיקיית האב לא יכולה להיות הריפו עצמו או בתוכו: ההעתקה תכלול את עצמה")
    return ws


# ------------------------------------------------------------------- ניקוי


def _on_rmtree_error(func, path, _exc):
    """ב-Windows קבצים ב-.git הם לקריאה בלבד ומחיקה נכשלת. מורידים את
    הסימון ומנסים שוב."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(path: Path) -> None:
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=lambda f, p, e: _on_rmtree_error(f, p, e))
    else:
        shutil.rmtree(path, onerror=lambda f, p, e: _on_rmtree_error(f, p, e))


# ------------------------------------------------------------------ sandbox


class Sandbox:
    """context manager: יוצר העתק ומנקה אותו תמיד, גם אם קרתה שגיאה.

    with Sandbox("C:/repos/x", workspace_dir="D:/work") as sb:
        sb.path        # ההעתק (תיקייה)
        sb.has_tests   # האם יש טסטים להריץ

    workspace_dir אופציונלי: תיקיית אב שבה ייווצר ההעתק. בתוכה נוצרת תת-תיקייה
    ייחודית (cra_sandbox_xxxx) ורק אותה מוחקים. בלי workspace_dir (וגם בלי
    WORKSPACE_DIR ב-.env) משתמשים בתיקייה הזמנית של מערכת ההפעלה.
    """

    def __init__(self, source_path: str, workspace_dir: str | None = None):
        self.source = validate_source(source_path)
        parent = workspace_dir or config.WORKSPACE_DIR
        self.workspace = _validate_workspace(parent, self.source) if parent else None
        self.is_single_file = self.source.is_file()
        self._tmp: Path | None = None  # התת-תיקייה הייחודית שיצרנו (ורק אותה מוחקים)
        self.path: Path | None = None  # ההעתק עצמו

    def __enter__(self) -> "Sandbox":
        self._tmp = Path(tempfile.mkdtemp(prefix=config.SANDBOX_PREFIX, dir=self.workspace))
        try:
            if self.is_single_file:
                self.path = self._tmp / "single_file"
                self.path.mkdir()
                shutil.copy2(self.source, self.path / self.source.name)
            else:
                self.path = self._tmp / self.source.name
                shutil.copytree(
                    self.source,
                    self.path,
                    ignore=shutil.ignore_patterns(*config.COPY_IGNORE_DIRS, "*.pyc"),
                )
        except BaseException:
            self.cleanup()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.cleanup()

    @property
    def has_tests(self) -> bool:
        if self.path is None or self.is_single_file:
            return False
        return _has_tests(self.path)

    @property
    def is_git_repo(self) -> bool:
        """דחיפה לענף אפשרית רק אם יש .git (בקובץ בודד אין)."""
        return self.path is not None and (self.path / ".git").exists()

    def _assert_ours(self, path: Path) -> None:
        """רשת ביטחון: מוחקים רק תיקייה שה-sandbox יצר, לא את תיקיית האב ולא את המקור."""
        if (
            not path.name.startswith(config.SANDBOX_PREFIX)
            or path == self.workspace
            or path == self.source
        ):
            raise RuntimeError(f"בטיחות: מסרבת למחוק {path}. זו לא תיקייה שה-sandbox יצר")

    def cleanup(self) -> None:
        if self._tmp is not None and self._tmp.exists():
            self._assert_ours(self._tmp)
            _rmtree(self._tmp)
        self._tmp = None
        self.path = None
