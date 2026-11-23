"""core/filtering.py: סינון חשדות בקוד רגיל (בלי מודל). פונקציות טהורות.

שלב 6: ב-before רק 5 מתוך 35 חשדות היו אמיתיים. המודל מחזיר כמעט תמיד משהו בכל קובץ.
הסינון כאן הוא החלק הדטרמיניסטי:
1. dedupe: חשדות באותו קובץ במרחק DEDUP_WINDOW שורות הם בדרך כלל אותו באג
   (ב-before: שורות 16 ו-17 ב-pip_unknown_command.py). נשאר החשד עם הביטחון הגבוה.
2. rank: לפי ביטחון (מהגבוה לנמוך), ואחר כך לפי הסדר המקורי.
3. cap: תקרה לכמות החשדות. כל חשד עולה קריאות למודל והרצת טסטים, ורוב החשדות הנמוכים שגויים.
"""
from __future__ import annotations

from core import config


def dedupe(suspects: list[dict], window: int | None = None) -> list[dict]:
    window = config.DEDUP_WINDOW if window is None else window
    kept: list[dict] = []
    for s in sorted(suspects, key=lambda s: -s.get("confidence", 0)):
        if not any(k["file"] == s["file"] and abs(k["line"] - s["line"]) <= window for k in kept):
            kept.append(s)
    order = {id(s): i for i, s in enumerate(suspects)}
    return sorted(kept, key=lambda s: order[id(s)])  # שומרים על הסדר המקורי


def rank(suspects: list[dict]) -> list[dict]:
    return sorted(suspects, key=lambda s: -s.get("confidence", 0))  # sorted יציב: שוויון = הסדר המקורי


def split_by_confidence(suspects: list[dict], minimum: int) -> tuple[list[dict], list[dict]]:
    """(מעל הסף, מתחת לסף)."""
    return [s for s in suspects if s.get("confidence", 0) >= minimum], [
        s for s in suspects if s.get("confidence", 0) < minimum
    ]
