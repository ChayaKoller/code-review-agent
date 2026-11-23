"""eval/metrics.py: חישוב המדדים מתוצאה אחת של הסוכן מול ה-ground truth. פונקציות טהורות.

הגדרות (מ-DECISIONS.md):
- "מצא" = חשד באותו קובץ, עד TOLERANCE שורות מהשורה האמיתית. מודדים גם שורה מדויקת.
- precision = כמה מהחשדות פוגעים בבאג אמיתי (חשד בקובץ נקי או בשורה רחוקה = false positive).
- "תיקן" = הטסט שנכשל לפני התיקון עובר אחריו, אף טסט אחר לא נשבר, ולא שונו קבצי טסט.
  ריפו בלי טסטים: אין שיעור תיקון (None), כי אין שופט אובייקטיבי.
"""
from __future__ import annotations

TOLERANCE = 3


def _hits(suspect: dict, bug: dict, tolerance: int) -> bool:
    return suspect["file"] == bug["file"] and abs(suspect["line"] - bug["line"]) <= tolerance


def detection(suspects: list[dict], bugs: list[dict], tolerance: int) -> dict:
    found = [b["id"] for b in bugs if any(_hits(s, b, tolerance) for s in suspects)]
    true_suspects = sum(1 for s in suspects if any(_hits(s, b, tolerance) for b in bugs))
    return {
        "found": found,
        "recall": len(found) / len(bugs) if bugs else None,
        "precision": true_suspects / len(suspects) if suspects else None,
        "false_positives": len(suspects) - true_suspects,
    }


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return any(p in ("tests", "test") for p in parts[:-1]) or name.startswith("test_") or name.endswith("_test.py")


def _base(test_id: str) -> str:
    """שם הטסט בלי הפרמטרים: 'a.py::t[x y]' -> 'a.py::t'.
    בטסט עם פרמטרים יש כמה מקרים, ו-pytest חותך את השם ברווח הראשון, ולכן משווים לפי השם בלבד.
    "תוקן" = אף מקרה של הטסט לא נכשל אחרי התיקון."""
    return test_id.split("[", 1)[0]


def fixed_bugs(bugs: list[dict], result: dict) -> list[str] | None:
    """אילו באגים תוקנו לפי הטסטים. None אם אין טסטים בריפו."""
    with_tests = [b for b in bugs if b.get("failing_test")]
    if not with_tests:
        return None
    if not result.get("verification"):
        return []  # לא הוצע (או לא הוחל) תיקון: הבאג לא תוקן. בלי זה הריפו לא נספר בכלל בממוצע
    if result.get("verification") != "tests":
        return None  # הטסטים לא רצו (למשל התקנה נכשלה): אין שופט
    if result.get("new_failures"):  # התיקון שבר טסט אחר: אף באג לא נחשב מתוקן
        return []
    if any(_is_test_path(e["file"]) for e in result.get("fixes", [])):
        return []  # שינוי בקובץ טסט פוסל
    before = {_base(t) for t in result.get("failing_tests", [])}
    after = {_base(t) for t in result.get("failed_after", [])}
    return [b["id"] for b in with_tests if _base(b["failing_test"]) in before and _base(b["failing_test"]) not in after]


def score(result: dict, bugs: list[dict]) -> dict:
    """כל המדדים לריפו אחד."""
    suspects = result.get("suspects", [])
    near = detection(suspects, bugs, TOLERANCE)
    exact = detection(suspects, bugs, 0)
    fixed = fixed_bugs(bugs, result)
    testable = [b for b in bugs if b.get("failing_test")]
    return {
        "bugs": len(bugs),
        "suspects": len(suspects),
        "recall": near["recall"],
        "precision": near["precision"],
        "recall_exact": exact["recall"],
        "precision_exact": exact["precision"],
        "false_positives": near["false_positives"],
        "found": near["found"],
        "fixed": fixed,
        "fix_rate": (len(fixed) / len(testable)) if fixed is not None else None,
    }
