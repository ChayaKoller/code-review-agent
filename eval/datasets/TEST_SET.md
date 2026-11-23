# test set: באגים אמיתיים (BugsInPy)

9 באגים מ-3 ריפוזיטוריז אמיתיים. כל אחד נבדק: הטסט נכשל בקוד הבאגי ועובר אחרי התיקון המקורי, ושום טסט אחר לא נשבר.

| id | קובץ:שורה | סוג | הבאג | הטסט שנכשל |
|---|---|---|---|---|
| `thefuck_1` | `thefuck/rules/pip_unknown_command.py`:15 | logic | regex [a-z]+ too narrow: command names with '+' or '-' are not matched | `test_get_new_command` |
| `thefuck_2` | `thefuck/utils.py`:121 | logic | PATH split on ':' instead of os.pathsep (breaks on Windows paths) | `test_get_all_executables_pathsep` |
| `thefuck_12` | `thefuck/rules/no_command.py`:9 | logic | match() accepts commands that exist; missing 'not which(...)' check | `test_not_match` |
| `thefuck_18` | `thefuck/rules/sudo.py`:24 | logic | rule matches a command that already starts with sudo (should return False) | `test_not_match` |
| `thefuck_25` | `thefuck/rules/mkdir_p.py`:13 | logic | regex anchored with ^, so 'hdfs dfs -mkdir x' (mkdir not at the start) is not fixed | `test_get_new_command` |
| `thefuck_27` | `thefuck/rules/open.py`:26 | logic | hard-coded slice script[5:] assumes 'open ' prefix; breaks for other open commands | `test_get_new_command` |
| `thefuck_32` | `thefuck/rules/ls_lah.py`:2 | logic | substring check 'ls' in script matches any command containing ls | `test_match` |
| `httpie_3` | `httpie/sessions.py`:104 | none-handling | header explicitly unset (value None) crashes on value.decode() | `TestSession::test_download_in_session` |
| `pysnooper_3` | `pysnooper/pysnooper.py`:26 | logic | write() uses undefined name output_path instead of output (NameError) | `test_file_output` |

## איך פריט בנוי
- `buggy_commit` / `fixed_commit`: מ-BugsInPy. `fix_commit` בבאג = קישור ל-commit התיקון ב-GitHub.
- ההעתק = `fixed_commit`, וקבצי `bug_files` מוחזרים לגרסה הבאגית (ב-commit הבאגי הטסט עוד לא קיים). מכין: `eval/snapshot.py`.
- `include`: מה שהסוכן סוקר = הקובץ הבאגי + 3 קבצים נקיים מאותה תיקייה (מודדים precision בלי מאות קריאות למודל).
- `python` + `extra_packages`: הריפוזיטוריז ישנים, רצים על Python 3.8 עם גרסאות ישנות של pytest.
- `line`: השורה הראשונה שהתיקון משנה בקובץ הבאגי. כשהתיקון רק מוסיף שורות (`note`), זו השורה שלפניה נוסף הקוד.

## הרצה
```
python -m eval.run_eval --dataset test                       # מקומי
python -m eval.run_eval --dataset test --only pysnooper_3    # פריט אחד (מומלץ להתחיל כך)
python -m eval.run_eval --dataset test --langsmith --name before
```
בפעם הראשונה: clone של 3 הריפוזיטוריז ל-`eval/.cache`, בניית `cra-runner:py3.8` ו-image לכל פריט (כמה דקות, צריך רשת).

## מגבלות (לכתוב ב-README)
- 8 מתוך 9 הם באגי לוגיקה, אחד None. אין off-by-one אמיתי שמשתחזר.
- 7 מתוך 9 מ-thefuck (קבצי rule קצרים), ולכן התוצאות מוטות לקוד מהסוג הזה.
- הסקירה על 4 קבצים ולא על ריפו שלם: recall כאן גבוה יותר מאשר בשימוש אמיתי.
- באג אמיתי שלא ידוע ב"קבצים הנקיים" ייספר כ-false positive.
