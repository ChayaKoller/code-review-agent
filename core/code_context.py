"""core/code_context.py: שולחים למודל רק את הקטע הרלוונטי, ולא את כל הקובץ.

הסיבה (שלב 6): למודל אין זיכרון, ולכן כל קריאה שולחת את כל מה שהיא צריכה מחדש.
אם כל קריאה שולחת את כל הקובץ, אותו קובץ נשלח שוב ושוב (חיפוש, אימות, תיקון).
לאימות ולתיקון מספיקה הפונקציה שבה נמצא החשד.

הפונקציה נמצאת עם ast (מבנה הקוד), ולא לפי ספירת שורות. מספרי השורות בקטע הם
המספרים המקוריים בקובץ, כך שהתיקון שהמודל מחזיר מתאים לקובץ המלא.
"""
from __future__ import annotations

import ast

FALLBACK_RADIUS = 15  # אם אין פונקציה סביב השורה (קוד ברמת המודול): 15 שורות לכל צד
MAX_SPAN_LINES = 120  # פונקציה ארוכה מאוד: חותכים סביב השורה


def enclosing_span(source: str, line: int) -> tuple[int, int]:
    """(שורה ראשונה, שורה אחרונה) של הפונקציה הפנימית ביותר שמכילה את line.
    כולל דקורטורים. אם אין פונקציה כזו, או שהקובץ לא מתפרק: טווח קבוע סביב השורה."""
    total = len(source.splitlines())
    best: tuple[int, int] | None = None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        tree = None
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                end = node.end_lineno or node.lineno
                if start <= line <= end and (best is None or end - start < best[1] - best[0]):
                    best = (start, end)  # הפנימית ביותר = הקצרה ביותר שמכילה את השורה
    if best is None or best[1] - best[0] + 1 > MAX_SPAN_LINES:
        radius = FALLBACK_RADIUS if best is None else MAX_SPAN_LINES // 2
        best = (max(1, line - radius), min(total, line + radius))
    return best


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """מאחד טווחים חופפים או צמודים, כדי שאותה פונקציה לא תישלח פעמיים."""
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def imports_span(source: str) -> tuple[int, int] | None:
    """שורות ה-import בראש הקובץ: בלעדיהן המודל לא יודע מה זה re או os בתוך הפונקציה."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    lines = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if not lines:
        return None
    return (lines[0].lineno, max(n.end_lineno or n.lineno for n in lines))


def excerpt(source: str, lines: list[int]) -> str:
    """הקטעים סביב השורות, ממוספרים במספרים המקוריים. בין קטעים: '...'."""
    src_lines = source.splitlines()
    spans = [enclosing_span(source, n) for n in lines]
    head = imports_span(source)
    if head:
        spans.append(head)
    parts = []
    for start, end in merge_spans(spans):
        parts.append("\n".join(f"{n}: {src_lines[n - 1]}" for n in range(start, end + 1)))
    return "\n...\n".join(parts)
