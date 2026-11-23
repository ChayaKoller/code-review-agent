"""core/repo_context.py: מה המודל צריך לדעת על שאר הריפו כדי להבין קובץ אחד.

הבעיה (ממצא 1 בביקורת): המודל רואה קובץ אחד בלי הקשר. ב-thefuck, למשל, הוא לא יודע מה זה
`Command` או מה מחזירה `which()`. לכן הוא מנחש "אולי זה None" (חשד הגנתי) ומפספס באגי לוגיקה.

הפתרון, בלי מודל ובלי להריץ קוד: מפרקים את הקובץ עם ast, מוצאים מה הוא מייבא מתוך הריפו עצמו,
ומביאים מהמודולים האלה רק את החתימות: שורת ה-def או ה-class, השורה הראשונה של ה-docstring,
והמתודות של מחלקה. לא את הגוף. כך ההקשר קצר (עד MAX_CONTEXT_LINES שורות) ונשלח פעם אחת לכל קובץ.

ספריות חיצוניות (re, os, requests) לא נכללות: המודל מכיר אותן, והקוד שלהן לא בהעתק.
"""
from __future__ import annotations

import ast
from pathlib import Path

MAX_CONTEXT_LINES = 60


def _module_file(root: Path, module: str, current: str, level: int) -> Path | None:
    """'thefuck.utils' -> root/thefuck/utils.py (או .../__init__.py). level>0 = ייבוא יחסי (from . import x)."""
    if level:
        base = Path(current).parent
        for _ in range(level - 1):
            base = base.parent
        parts = list(base.parts) + (module.split(".") if module else [])
    else:
        parts = module.split(".")
    for candidate in (root.joinpath(*parts).with_suffix(".py"), root.joinpath(*parts, "__init__.py")):
        if candidate.is_file():
            return candidate
    return None


def _signature(node: ast.AST, lines: list[str]) -> list[str]:
    """שורת הכותרת (גם כשהיא נמשכת על כמה שורות) + שורה ראשונה של ה-docstring."""
    body_start = node.body[0].lineno if node.body else node.lineno + 1
    header = [ln.rstrip() for ln in lines[node.lineno - 1 : max(node.lineno, body_start - 1)]]
    doc = ast.get_docstring(node)
    if doc:
        indent = " " * (node.col_offset + 4)
        header.append(f'{indent}"""{doc.strip().splitlines()[0]}"""')
    return header


def _describe(node: ast.AST, lines: list[str]) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _signature(node, lines)
    if isinstance(node, ast.ClassDef):
        out = _signature(node, lines)
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out += _signature(item, lines)
            elif isinstance(item, (ast.Assign, ast.AnnAssign)):  # שדות של המחלקה (למשל namedtuple / dataclass)
                out.append(lines[item.lineno - 1].rstrip())
        return out
    if isinstance(node, (ast.Assign, ast.AnnAssign)):  # קבוע ברמת המודול
        return [lines[node.lineno - 1].rstrip()]
    return []


def _definitions(path: Path) -> dict[str, list[str]]:
    """שם -> תיאור קצר, לכל הגדרה ברמה העליונה של המודול."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return {}
    lines = source.splitlines()
    found: dict[str, list[str]] = {}
    for node in tree.body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        for name in names:
            found[name] = _describe(node, lines)
    return found


def build_context(root: str | Path, rel: str, source: str, max_lines: int = MAX_CONTEXT_LINES) -> str:
    """ההקשר לקובץ rel: חתימות של מה שהוא מייבא מתוך הריפו. ריק אם אין."""
    root = Path(root)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    blocks: list[str] = []
    used = 0
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue  # `import x` מביא מודול שלם: יותר מדי, ובדרך כלל זו ספרייה חיצונית
        path = _module_file(root, node.module or "", rel, node.level)
        if path is None:
            continue
        defs = _definitions(path)
        module = path.relative_to(root).as_posix()
        for alias in node.names:
            desc = defs.get(alias.name)
            if not desc or used + len(desc) > max_lines:
                continue
            blocks.append(f"# from {module}\n" + "\n".join(desc))
            used += len(desc)
    return "\n\n".join(blocks)
