"""core/config.py: הגדרות מרכזיות. רק ערכים, בלי לוגיקה."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # טוען את .env אל os.environ

# --- מודל ---
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-3.1-flash-lite")

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
