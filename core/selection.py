"""core/selection.py: בחירת הקבצים לסקירה מתוך ה-sandbox.

שני מצבים:
- אוטומטי (ברירת מחדל): סורק ובוחר לפי כללים (בלי טסטים, לא קצר מדי, לא ארוך מדי).
- ידני (include): המשתמש בחר קבצים בעצמו. הבחירה שלו גוברת על הכללים האוטומטיים.

מחזיר גם את הקבצים שדולגו ולמה, כדי שאפשר יהיה לראות למה קובץ לא נסקר.
הנתיבים תמיד יחסיים לשורש ההעתק ובפורמט עם / (גם ב-Windows).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from core import config


@dataclass
class Selection:
    selected: list[str] = field(default_factory=list)  # נתיבים יחסיים, ממוינים
    skipped: dict[str, str] = field(default_factory=dict)  # נתיב -> סיבת הדילוג


def _norm(name: str) -> str:
    """'utils\\a.py' ו-'./utils/a.py' הם אותו נתיב."""
    path = name.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _is_test_file(rel: Path) -> bool:
    # בודקים רק את החלקים היחסיים: אם שורש ההעתק עצמו נקרא "tests", זה לא משנה
    if any(part in config.TEST_DIR_NAMES for part in rel.parts[:-1]):
        return True
    return any(rel.match(pattern) for pattern in config.TEST_FILE_PATTERNS)


def _line_count(path: Path) -> int | None:
    """מספר שורות, או None אם אי אפשר לקרוא כ-UTF-8."""
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except UnicodeDecodeError:
        return None


def _scan(root: Path, result: Selection) -> None:
    """מצב אוטומטי."""
    for dirpath, dirnames, filenames in os.walk(root):
        # מדלגים על תיקיות שלמות (ועל .git, שמועתק אבל אין בו קוד לסקירה)
        dirnames[:] = [d for d in dirnames if d not in config.COPY_IGNORE_DIRS and d != ".git"]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(root)
            key = rel.as_posix()

            if _is_test_file(rel):
                result.skipped[key] = "קובץ טסט (אסור לשנות)"
                continue
            lines = _line_count(path)
            if lines is None:
                result.skipped[key] = "לא ניתן לקרוא (קידוד לא תקין)"
            elif lines < config.MIN_FILE_LINES:
                result.skipped[key] = f"קצר מדי ({lines} שורות)"
            elif lines > config.MAX_FILE_LINES:
                result.skipped[key] = f"ארוך מדי ({lines} שורות)"
            else:
                result.selected.append(key)


def _add_explicit(root: Path, root_resolved: Path, name: str, result: Selection) -> None:
    """מצב ידני: הקובץ שהמשתמש בחר נכנס גם אם הוא קצר, ארוך או נקרא test_*.py.
    רק בדיקות בטיחות בסיסיות: בתוך ההעתק, קיים, פייתון, קריא, לא ריק."""
    key = _norm(name)
    target = (root / key).resolve()
    try:
        rel = target.relative_to(root_resolved)
    except ValueError:
        result.skipped[key] = "מחוץ לתיקיית העבודה"
        return
    rel_key = rel.as_posix()
    if ".git" in rel.parts:
        reason = "בתוך .git"
    elif not target.is_file():
        reason = "הקובץ לא קיים"
    elif target.suffix != ".py":
        reason = "לא קובץ פייתון"
    else:
        lines = _line_count(target)
        if lines is None:
            reason = "לא ניתן לקרוא (קידוד לא תקין)"
        elif lines == 0:
            reason = "קובץ ריק"
        else:
            if rel_key not in result.selected:
                result.selected.append(rel_key)
            return
    result.skipped[rel_key] = reason


def select_files(
    sandbox_path: str,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Selection:
    """סורק את ההעתק ובוחר קבצי .py לסקירה. לא משנה שום קובץ.

    sandbox_path: תיקיית ההעתק, או נתיב של קובץ בודד.
    include: אם ניתן, נבחרים בדיוק הקבצים האלה (בלי הכללים האוטומטיים).
    exclude: קבצים להוצאה מהבחירה.
    """
    root = Path(sandbox_path)
    if root.is_file():
        include, root = [root.name], root.parent

    result = Selection()
    if include:
        root_resolved = root.resolve()
        for name in include:
            _add_explicit(root, root_resolved, name, result)
    else:
        _scan(root, result)

    for name in exclude or []:
        key = _norm(name)
        if key in result.selected:
            result.selected.remove(key)
            result.skipped[key] = "הוצא ידנית"

    result.selected.sort()
    return result
