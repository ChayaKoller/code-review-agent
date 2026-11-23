"""eval/snapshot.py: הכנת העתק מקומי של ריפו אמיתי בנקודת הבאג (ל-test set).

באג אמיתי (BugsInPy) מוגדר על ידי שני commits: הבאגי ו-commit התיקון.
ב-commit הבאגי הטסט שתופס את הבאג לרוב עוד לא קיים, ולכן ההעתק הוא:
commit התיקון (כולל הטסט החדש), שבו קבצי הקוד של הבאג (bug_files) מוחזרים לגרסה הבאגית.
זה בדיוק מה ש-BugsInPy עושה.

ההכנה רק מריצה git (בלי hooks), לא מריצה קוד של הריפו. אחרי ההכנה מוחקים את origin,
כך שגם בטעות אי אפשר לדחוף מההעתק.
"""
from __future__ import annotations

from pathlib import Path

from core import git_ops

CACHE_DIR = Path(__file__).parent / ".cache"  # clone אחד לכל ריפו, נשמר בין הרצות (ב-.gitignore)
_IDENTITY = ["-c", "user.name=cra-eval", "-c", "user.email=cra-eval@localhost"]


def cached_clone(url: str, cache_dir: Path = CACHE_DIR) -> Path:
    """clone מלא פעם אחת (כולל היסטוריה, כי צריך commits ישנים)."""
    path = cache_dir / git_ops.repo_name_from_url(url)
    if not (path / ".git").exists():
        cache_dir.mkdir(parents=True, exist_ok=True)
        git_ops.clone(url, path)
    return path


def prepare_snapshot(item: dict, dest: Path, cache_dir: Path = CACHE_DIR) -> Path:
    """יוצר ב-dest (תיקייה שעוד לא קיימת) ריפו git: קוד באגי + טסטים מ-commit התיקון."""
    cache = cached_clone(item["repo"], cache_dir)
    # core.autocrlf=false: אותם קבצים ואותם מספרי שורות כמו בלינוקס (ב-Docker)
    git_ops.run_git(["-c", "core.autocrlf=false", "clone", "-q", "--no-checkout", str(cache), str(dest)])
    git_ops.run_git(["config", "core.autocrlf", "false"], cwd=dest)
    git_ops.run_git(["checkout", "-q", item["fixed_commit"]], cwd=dest)
    git_ops.run_git(["checkout", "-q", item["buggy_commit"], "--", *item["bug_files"]], cwd=dest)
    git_ops.run_git([*_IDENTITY, "commit", "-q", "-m", "eval snapshot"], cwd=dest)
    git_ops.run_git(["remote", "remove", "origin"], cwd=dest)
    return dest
