"""core/git_ops.py: פעולות git, רק בתוך ההעתק.

clone, יצירת ענף ו-commit. הדחיפה עצמה לא כאן, אלא ב-publish.py,
כדי שתהיה פונקציה אחת ונפרדת שרק היא שולחת משהו החוצה.

בטיחות: כל פקודה רצה עם core.hooksPath שמצביע על תיקייה ריקה, ולכן hooks
(סקריפטים שהריפו או המחשב מגדירים) לא רצים על המחשב. git לא מריץ את הקוד של הריפו.
"""
from __future__ import annotations

import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from core import config


class GitError(RuntimeError):
    """פקודת git נכשלה. ההודעה כוללת את סוף הפלט של git."""


_NO_HOOKS_DIR: str | None = None


def _no_hooks_dir() -> str:
    """תיקייה ריקה שנוצרת פעם אחת. git יחפש בה hooks ולא ימצא."""
    global _NO_HOOKS_DIR
    if _NO_HOOKS_DIR is None:
        _NO_HOOKS_DIR = tempfile.mkdtemp(prefix="cra_nohooks_")
    return _NO_HOOKS_DIR


def run_git(args: list[str], cwd: str | Path | None = None, timeout: int | None = None) -> str:
    """מריץ git ומחזיר את הפלט. זורק GitError אם נכשל."""
    cmd = ["git", "-c", f"core.hooksPath={_no_hooks_dir()}", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or config.GIT_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        raise GitError("git לא נמצא. להתקין Git ולוודא שהפקודה git עובדת בטרמינל") from None
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} לקח יותר מדי זמן") from None
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-10:])
        raise GitError(f"git {args[0]} נכשל:\n{tail}")
    return proc.stdout


def is_git_url(source: str) -> bool:
    return source.strip().startswith(config.GIT_URL_PREFIXES)


def repo_name_from_url(url: str) -> str:
    """'https://github.com/a/my-repo.git' -> 'my-repo'"""
    name = url.rstrip("/").split("/")[-1].split(":")[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name or "repo"


def clone(url: str, dest: Path) -> None:
    """מוריד את הריפו לתיקייה dest (שעוד לא קיימת).

    בלי submodules (קוד מריפוזיטוריז אחרים), ו-symlinks נשמרים כקבצי טקסט,
    כדי שקישור לקובץ במחשב (למשל מפתח) לא ייקרא ולא יישלח למודל.
    """
    run_git(
        ["-c", "core.symlinks=false", "clone", "--no-recurse-submodules", "--", url, str(dest)],
        timeout=config.GIT_TIMEOUT_SECONDS,
    )


def new_branch_name() -> str:
    return config.BRANCH_PREFIX + datetime.now().strftime("%Y%m%d-%H%M%S")


def create_branch(repo: Path, name: str) -> None:
    run_git(["checkout", "-b", name], cwd=repo)


def commit_files(repo: Path, files: list[str], message: str) -> str:
    """commit רק של הקבצים שתוקנו (לא של קבצים אחרים שנוצרו בהעתק). מחזיר את מזהה ה-commit."""
    run_git(["add", "--", *files], cwd=repo)
    run_git(["commit", "-m", message], cwd=repo)
    return run_git(["rev-parse", "HEAD"], cwd=repo).strip()
