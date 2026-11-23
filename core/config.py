"""core/config.py: הגדרות מרכזיות. רק ערכים, בלי לוגיקה."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # טוען את .env אל os.environ

# --- מודל ---
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")
# מודל למאמת (check_suspect). ריק = אותו מודל. מודל אחר מקטין את הסיכוי שהמאמת "יסכים עם עצמו"
CHECK_MODEL_NAME = os.getenv("CHECK_MODEL_NAME") or None
LLM_TEMPERATURE = 0  # תשובות יציבות ככל האפשר
LLM_MAX_RETRIES = 3  # ניסיונות חוזרים בתוך הספרייה של Gemini
# אחרי זה: עוד ניסיונות שלנו, בהמתנה שמכפילה את עצמה (10, 20, 40, 80 שניות)
LLM_BUSY_RETRIES = 5
LLM_RETRY_FIRST_DELAY = 10
# זמן מקסימלי לקריאה אחת למודל. בלי זה קריאה שנתקעה (חיבור שלא עונה) מחכה לנצח.
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))

# --- בחירת קבצים לסקירה ---
MIN_FILE_LINES = 5
MAX_FILE_LINES = 600  # ערך התחלתי, נכוון אחרי שנראה קבצים אמיתיים
TEST_DIR_NAMES = {"tests", "test"}
TEST_FILE_PATTERNS = ("test_*.py", "*_test.py", "conftest.py")

# --- sandbox ---
SANDBOX_PREFIX = "cra_sandbox_"
# תיקיית אב אופציונלית להעתקים (ב-.env: WORKSPACE_DIR). ריק = התיקייה הזמנית של מערכת ההפעלה.
# ה-sandbox יוצר בתוכה תת-תיקייה ייחודית, ומוחק רק אותה. תיקיית האב לא נמחקת לעולם.
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR") or None
# ה-.git לא ברשימה: מעתיקים אותו, כדי ליצור ענף ו-commit בהעתק
COPY_IGNORE_DIRS = {
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    ".tox",
}

# --- git: הורדת ריפו מקישור, ענף ודחיפה ---
GIT_TIMEOUT_SECONDS = 300
BRANCH_PREFIX = "cra/fix-"  # שם הענף: cra/fix-20261123-153000
# קישורים שמותר לשכפל. file:// ונתיבים מקומיים עוברים דרך העתקה רגילה, לא דרך clone.
GIT_URL_PREFIXES = ("https://", "http://", "ssh://", "git@")

# --- Docker: כל הרצת קוד של הפרויקט הנבדק קורית שם ורק שם ---
DOCKER_DIR = Path(__file__).resolve().parent.parent / "docker"
DOCKER_PYTHON_VERSION = os.getenv("DOCKER_PYTHON_VERSION", "3.12")
DOCKER_IMAGE = os.getenv("DOCKER_IMAGE", f"cra-runner:py{DOCKER_PYTHON_VERSION}")
# תוספת ל-pip בזמן בניית ה-image. בנטפרי אולי צריך:
# DOCKER_PIP_EXTRA_ARGS=--trusted-host pypi.org --trusted-host files.pythonhosted.org
DOCKER_PIP_EXTRA_ARGS = os.getenv("DOCKER_PIP_EXTRA_ARGS", "")
# הספריות של הריפו הנבדק: נבנה image לכל ריפו (מעל image הבסיס) מקבצי requirements שבשורש שלו.
# ההרצה עצמה בלי רשת, ולכן הספריות חייבות להיות כבר בתוך ה-image.
DOCKER_DEPS_IMAGE_REPO = "cra-deps"
REQUIREMENT_FILE_NAMES = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements_dev.txt",
    "dev-requirements.txt",
    "requirements-test.txt",
    "requirements_test.txt",
    "test-requirements.txt",
)
# ריפו שמוגדר כחבילה (pyproject.toml / setup.py / setup.cfg): מתקינים אותו ב-image יחד עם
# התלויות שלו ועם ה-extras של הטסטים. כך ריפו שהורד מקישור מקבל את הספריות שלו גם בלי requirements.
# זה מריץ את קוד ההתקנה של הריפו, אבל רק בתוך Docker בזמן הבנייה. כדי לכבות: DOCKER_INSTALL_PROJECT=false
DOCKER_INSTALL_PROJECT = os.getenv("DOCKER_INSTALL_PROJECT", "true").lower() not in ("0", "false", "no")
PROJECT_METADATA_FILES = ("pyproject.toml", "setup.py", "setup.cfg")
PROJECT_TEST_EXTRAS = ("test", "tests", "testing")
DOCKER_CONTAINER_PREFIX = "cra-run-"
DOCKER_MEMORY = "512m"
DOCKER_CPUS = "1"
DOCKER_PIDS_LIMIT = "128"
DOCKER_BUILD_TIMEOUT_SECONDS = 900
DOCKER_COMPILE_TIMEOUT_SECONDS = 60
TEST_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 100_000  # פלט ארוך יותר נחתך (נשאר הסוף, שם הכשלים)

# --- אישור אנושי ---
MAX_REJECT_ATTEMPTS = 2

# --- שלב 6: סינון חשדות ובדיקת תיקונים ---
DEDUP_WINDOW = 3  # חשדות באותו קובץ במרחק עד 3 שורות = אותו באג
SUSPECT_MIN_CONFIDENCE = 3  # ביטחון 1-5 מהמודל. מתחת לסף לא מאמתים בכלל (חוסך קריאות)
MAX_SUSPECTS_TO_VERIFY = 6  # כמה חשדות (לפי ביטחון) נשלחים למאמת
MAX_SUSPECTS = 3  # תקרה לחשדות שמגיעים לתיקון
MAX_DOUBTFUL = 2  # חשדות שהמאמת זרק ומקבלים הזדמנות בטסטים (רק כשיש טסטים)
FIX_RETRIES = 1  # תיקון ששבר טסט מקבל עוד ניסיון אחד עם פלט הכשל
FAILURE_FEEDBACK_CHARS = 2000  # כמה מפלט הכשל שולחים למודל בניסיון השני
