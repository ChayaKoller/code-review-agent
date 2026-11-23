"""scripts/docker_check.py: בונה את ה-image ובודק ש-Docker מוכן.

מריצים מהשורש:
    python -m scripts.docker_check                 # בונה את image הבסיס אם חסר
    python -m scripts.docker_check C:\\repos\\x    # בונה גם את ה-image עם הספריות של הריפו
"""
import sys

from core import config
from core.docker_runner import DockerError, DockerRunner, find_requirement_files


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # מונע שגיאת קידוד של עברית בטרמינל
    runner = DockerRunner()
    try:
        if not runner.is_available():
            print("Docker לא זמין (לא מותקן או שלא רץ). להפעיל את Docker Desktop.")
            return 1
        if runner.image_exists():
            print(f"image הבסיס כבר קיים: {runner.image}")
        else:
            print(f"בונה את image הבסיס ({runner.image}). זה דורש רשת וייקח כמה דקות...")
            runner.build_image()
            print("נבנה.")
        if len(argv) > 1:
            repo = argv[1]
            files = find_requirement_files(repo)
            print(f"קבצי requirements שנמצאו ב-{repo}: {files or 'אין'}")
            tag = runner.prepare_image(repo)
            print(f"image להרצה: {tag}")
    except DockerError as exc:
        print(exc)
        return 1
    print("מוכן. להרצת בדיקות Docker אמיתיות: python -m pytest tests/test_docker_real.py -v")
    print(f"(הגדרות: Python {config.DOCKER_PYTHON_VERSION}, בלי רשת בהרצה)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
