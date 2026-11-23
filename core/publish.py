"""core/publish.py: הפעולה היחידה ששולחת משהו החוצה: push של הענף עם התיקון.

נקראת רק מהצומת publish, שרץ רק אחרי אישור שני של המשתמשת.
הכללים: רק ענף חדש של הסוכן (קידומת BRANCH_PREFIX), אף פעם לא main, אף פעם לא force.
ההרשאה היא של המשתמשת (Git Credential Manager או token שהוגדר ב-git), לא של הסוכן.
"""
from __future__ import annotations

from pathlib import Path

from core import config
from core.git_ops import GitError, run_git


class PublishError(RuntimeError):
    """הדחיפה נכשלה או נחסמה בגלל כלל בטיחות."""


def push_branch(sandbox_path: str | Path, branch: str) -> str:
    """דוחף את הענף מההעתק ל-origin. מחזיר הודעה למשתמשת."""
    repo = Path(sandbox_path)
    if not branch.startswith(config.BRANCH_PREFIX):
        raise PublishError(f"בטיחות: דוחפים רק ענף של הסוכן ({config.BRANCH_PREFIX}...), לא {branch}")
    try:
        current = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo).strip()
        if current != branch:
            raise PublishError(f"בטיחות: ההעתק נמצא על {current} ולא על {branch}")
        origin = run_git(["remote", "get-url", "origin"], cwd=repo).strip()
        # refs/heads/<branch>:refs/heads/<branch> במפורש: אותו שם ענף בצד השני, בלי force
        run_git(["push", "--set-upstream", "origin", f"refs/heads/{branch}:refs/heads/{branch}"], cwd=repo)
    except GitError as exc:
        raise PublishError(
            f"הדחיפה נכשלה. אולי אין הרשאת כתיבה לריפו הזה, או שאין origin.\n{exc}"
        ) from None
    return f"נדחף לענף {branch} ב-{origin}"
