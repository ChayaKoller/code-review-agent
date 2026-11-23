"""core/state.py: המבנה של ה-State שעובר בין הצמתים בגרף.

הכול כאן ערכים פשוטים (מחרוזות, מספרים, רשימות ומילונים), כדי שה-checkpointer
יוכל לשמור את ה-State כשהגרף נעצר ב-interrupt.
"""
from typing import TypedDict

# רמות אימות של תיקון. מוצגות למשתמש ליד ה-diff, כדי שלא יהיה רושם שתיקון הוכח כשלא הוכח.
VERIFY_TESTS = "tests"  # הטסטים של הריפו רצו ב-Docker
VERIFY_COMPILE = "compile_only"  # אין טסטים (או שאי אפשר להריץ אותם): נבדק רק שהקוד מתקמפל

# החלטות של המשתמשת
APPROVE = "approve"
REJECT = "reject"
REJECT_WITH_NOTE = "reject_with_note"
PUSH = "push"
NO_PUSH = "no_push"


class Suspect(TypedDict):
    """חשד לבאג אחד."""

    file: str  # נתיב יחסי לשורש הריפו, למשל "app/utils.py"
    line: int  # מספר שורה, מתחילים לספור מ-1
    bug_type: str  # off_by_one / none_handling / logic
    reason: str  # הסבר קצר של המודל


class Edit(TypedDict):
    """תיקון אחד: החלפה של טווח שורות בקוד חדש."""

    file: str
    start_line: int  # כולל
    end_line: int  # כולל
    new_code: str  # הקוד שמחליף את השורות (יכול להיות כמה שורות)
    explanation: str


class ReviewState(TypedDict, total=False):
    # --- קלט ---
    repo_path: str  # המקור: קישור לגיט, תיקייה או קובץ .py. לא כותבים אליו לעולם
    include: list[str]  # בחירה ידנית של קבצים (אופציונלי)
    exclude: list[str]  # קבצים להוצאה (אופציונלי)

    # --- sandbox ---
    work_dir: str  # התיקייה הייחודית (cra_sandbox_xxxx). רק אותה מוחקים בסוף
    sandbox_path: str  # ההעתק שעובדים עליו (בתוך work_dir)
    branch_name: str  # הענף שנוצר בהעתק
    is_git_repo: bool  # יש .git: אפשר commit ודחיפה
    has_tests: bool  # יש טסטים להריץ
    single_file: str  # אם המקור הוא קובץ .py בודד: שם הקובץ. אחרת ריק

    # --- בחירה ואיתור ---
    files: list[str]  # הקבצים שנבחרו לסקירה (נתיבים יחסיים)
    skipped: dict[str, str]  # קבצים שדולגו -> הסיבה
    suspects: list[Suspect]  # החשודים, מכל הקבצים
    failing_tests: list[str]  # טסטים שנכשלו כבר לפני התיקון (כדי לא להאשים בהם את התיקון)

    # --- תיקון ואישור ראשון ---
    fixes: list[Edit]
    diff: str  # ה-diff שמוצג למשתמשת
    decision: str  # APPROVE / REJECT / REJECT_WITH_NOTE
    rejection_note: str
    attempts: int  # כמה פעמים הוצע תיקון

    # --- אימות ואישור שני ---
    commit_sha: str  # ה-commit בענף בהעתק
    verification: str  # VERIFY_TESTS או VERIFY_COMPILE
    verify_ok: bool
    verify_summary: str  # טקסט למשתמשת
    new_failures: list[str]  # טסטים שעברו לפני התיקון ונכשלו אחריו
    push_decision: str  # PUSH / NO_PUSH

    # --- תוצאה ---
    pushed: bool
    result: str  # סיכום סופי למשתמשת
