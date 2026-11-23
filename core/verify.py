"""core/verify.py: בדיקת תיקון בתוך Docker.

יש טסטים: מריצים אותם. אין טסטים (או שאי אפשר להריץ אותם): בודקים קומפילציה בלבד.
הסיכום (summary) נכתב למשתמש, והוא אומר במפורש כשהתיקון לא הוכח בטסטים.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.docker_runner import CompileResult, DockerRunner, TestRun
from core.sandbox import Sandbox
from core.state import VERIFY_COMPILE, VERIFY_TESTS


@dataclass
class Verification:
    level: str  # VERIFY_TESTS או VERIFY_COMPILE
    ok: bool
    summary: str  # טקסט קצר להצגה למשתמש
    tests: TestRun | None = None
    compile: CompileResult | None = None


def _tests_result(run: TestRun) -> Verification:
    if run.passed:
        return Verification(VERIFY_TESTS, True, "כל הטסטים עברו.", tests=run)
    if run.failed:
        shown = ", ".join(run.failed[:5])
        more = f" (ועוד {len(run.failed) - 5})" if len(run.failed) > 5 else ""
        files = ", ".join(run.failed_files)
        summary = f"נכשלו {len(run.failed)} טסטים בקבצים: {files}. לדוגמה: {shown}{more}"
        return Verification(VERIFY_TESTS, False, summary, tests=run)
    summary = f"הרצת הטסטים נכשלה (קוד יציאה {run.returncode}) בלי טסט שנכשל בשם. ראי את הפלט."
    return Verification(VERIFY_TESTS, False, summary, tests=run)


def _compile_result(comp: CompileResult, prefix: str, tests: TestRun | None) -> Verification:
    if comp.ok:
        summary = (
            prefix
            + "הקוד מתקמפל, אבל לא נבדק בטסטים: לא הוכח שהתיקון נכון. יש לבדוק את ה-diff בעצמך."
        )
        return Verification(VERIFY_COMPILE, True, summary, tests=tests, compile=comp)
    name, err = next(iter(comp.errors.items()))
    where = f" בשורה {err['line']}" if err.get("line") else ""
    summary = f"{prefix}שגיאת תחביר ב-{name}{where}: {err.get('msg')}"
    return Verification(VERIFY_COMPILE, False, summary, tests=tests, compile=comp)


def verify(
    sandbox: Sandbox,
    runner: DockerRunner,
    files: list[str],
    extra_packages: list[str] | None = None,
) -> Verification:
    """files: הקבצים ששונו (נתיבים יחסיים), לבדיקת קומפילציה."""
    prefix = ""
    run = None
    if sandbox.has_tests:
        run = runner.run_tests(sandbox.path, extra_packages=extra_packages)
        if run.timed_out:
            return Verification(VERIFY_TESTS, False, "הטסטים לא הסתיימו בזמן (timeout).", tests=run)
        if run.missing_modules:
            libs = ", ".join(run.missing_modules)
            prefix = f"הטסטים לא רצו: חסרות ספריות בסביבת ההרצה ({libs}). "
        elif run.no_tests:
            prefix = "pytest לא מצא טסטים להרצה. "
        else:
            return _tests_result(run)

    if not files:
        raise ValueError("verify: לא התקבלו קבצים לבדיקת קומפילציה")
    comp = runner.check_compile(sandbox.path, files)
    return _compile_result(comp, prefix, run)
