# הקמה ב-Windows (PowerShell)

מתוך תיקיית הפרויקט `code-review-agent`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

אם PowerShell חוסם את ההפעלה של הסביבה:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

אחר כך פותחים את `.env` ומדביקים את המפתחות:

- `GOOGLE_API_KEY` – המפתח החינמי של Gemini (מ-Google AI Studio).
- `LANGSMITH_API_KEY` – נוצר בחשבון חינמי ב-smith.langchain.com (Settings > API Keys).

בדיקה שהכול תקין:

```powershell
python -m pytest tests -q --ignore=tests/test_docker_real.py   # בלי רשת ובלי מפתחות
python -m scripts.docker_check                                  # Docker מוכן ו-image הבסיס נבנה
```

הפלט הצפוי: כל הבדיקות עוברות (`passed`), ו-`docker_check` מסתיים בלי שגיאה.

**חשוב:** הקובץ `.env` לא עולה ל-git (הוא ב-`.gitignore`). לא מדביקים מפתחות בצ'אט ולא בקוד.
