"""core/state.py: המבנה של ה-State שעובר בין הצמתים בגרף."""
from typing import TypedDict

# רמות אימות של תיקון. מוצגות למשתמש ליד ה-diff, כדי שלא יהיה רושם שתיקון הוכח כשלא הוכח.
VERIFY_TESTS = "tests"  # הטסטים של הריפו רצו ב-Docker
VERIFY_COMPILE = "compile_only"  # אין טסטים (או שאי אפשר להריץ אותם): נבדק רק שהקוד מתקמפל


class Suspect(TypedDict):
    """שורה חשודה אחת: באיזה קובץ ובאיזו שורה.

    בשלב 3 אפשר להוסיף לכאן שדות (סוג באג, הסבר) בלי לשנות את שאר ה-State.
    """

    file: str  # נתיב יחסי לשורש הריפו, למשל "app/utils.py"
    line: int  # מספר שורה, מתחילים לספור מ-1


class ReviewState(TypedDict):
    # --- ריפו ו-sandbox ---
    repo_path: str  # המקור (ריפו או קובץ בודד). לא כותבים אליו, רק דוחפים ממנו (אחרי אישור שני)
    sandbox_path: str  # ההעתק הזמני שעובדים עליו
    branch_name: str  # שם הענף שנוצר בהעתק
    has_tests: bool  # האם יש בהעתק טסטים להריץ

    # --- בחירה ואיתור ---
    files: list[str]  # הקבצים שנבחרו לסקירה (נתיבים יחסיים)
    suspects: list[Suspect]  # החשודים, מכל הקבצים

    # --- הרצת טסטים ---
    failing_tests: list[str]  # למשל "tests/test_utils.py::test_page" (הקובץ לפני ::)

    # --- תיקון ואישור (שלב 3 ואילך) ---
    old_line: str
    new_line: str
    verification: str  # VERIFY_TESTS או VERIFY_COMPILE: איך נבדק התיקון
    decision: str  # אישור ראשון: על ה-diff
    push_decision: str  # אישור שני: על הדחיפה
    rejection_note: str
    attempts: int
