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
python verify_bugs.py
```

הפלט הצפוי: חמש שורות `OK` ו-`ALL GOOD`.

**חשוב:** הקובץ `.env` לא עולה ל-git (הוא ב-`.gitignore`). לא מדביקים מפתחות בצ'אט ולא בקוד.
