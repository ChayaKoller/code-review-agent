"""ui/flow.py: מצב כל צומת בגרף, בשביל מפת התהליך בצד המסך. אין כאן import של streamlit.

הקלט: אילו צמתים כבר רצו (AgentSession.visited) ומה המצב עכשיו (רץ / מחכה לך / הסתיים).
הפלט: לכל צומת, לפי הסדר בגרף, אחד המצבים:
  done     - רץ והסתיים (בסבב הנוכחי, לצמתים שבתוך לולאת הסבבים)
  running  - רץ עכשיו
  waiting  - הגרף עצר כאן ב-interrupt ומחכה לך
  pending  - עוד לא הגיע אליו
  skipped  - הריצה הסתיימה והצומת לא רץ (למשל לא היו חשדות, או שלא דחפת)

סבבים (v13): verify_fix -> next_round -> find_suspects, כשהתיקון שיפר והטסטים עדיין נכשלים.
בסבב חדש, צמתי הלולאה (ROUND_NODES) מתחילים שוב כ-pending, ו-runs סופר כמה פעמים כל צומת רץ.
"""
from __future__ import annotations

from dataclasses import dataclass

# אותו סדר כמו ב-core/graph.py (המסלול הראשי)
ORDER = [
    ("prepare", "הכנת העתק", "clone לתיקייה זמנית וענף חדש"),
    ("select", "בחירת קבצים", "אילו קבצי .py נסקרים"),
    ("find_suspects", "איתור חשדות", "מודל, סינון ומאמת"),
    ("baseline", "טסטים לפני", "Docker: מה נכשל כבר עכשיו (סבב 1)"),
    ("propose_fix", "הצעת תיקון", "המודל מציע diff"),
    ("screen_fixes", "בדיקת כל תיקון", "Docker: כל קובץ לבד"),
    ("review_fix", "אישור ראשון", "interrupt: על ה-diff"),
    ("apply_fix", "החלת תיקון", "commit בענף בהעתק"),
    ("verify_fix", "אימות", "Docker: טסטים או קומפילציה"),
    ("next_round", "סבב נוסף?", "שיפר ועדיין נכשלים טסטים: חוזרים לאיתור"),
    ("review_push", "אישור שני", "interrupt: על הדחיפה"),
    ("publish", "דחיפה", "push לענף cra/fix-*"),
    ("finish", "סיום", "מחיקת ההעתק וסיכום"),
]
INTERRUPTS = {"review_fix", "review_push"}
ROUND_NODES = {"find_suspects", "propose_fix", "screen_fixes", "review_fix", "apply_fix", "verify_fix"}

# אחרי תשובה ב-interrupt, לאן הגרף ממשיך (כמו ה-route-ים ב-core/graph.py).
# בסבב 2 ומעלה, דחייה הולכת ל-review_push (יש כבר commit מסבב קודם) ולא ל-finish.
NEXT_AFTER_ANSWER = {
    "approve": "apply_fix",
    "reject_with_note": "propose_fix",
    "reject": "finish",
    "push": "publish",
    "no_push": "finish",
}


def heading_to(decision: str, round_no: int = 1) -> str:
    if decision == "reject" and round_no > 1:
        return "review_push"
    return NEXT_AFTER_ANSWER[decision]


@dataclass
class NodeState:
    node: str
    label: str
    hint: str
    state: str  # done / running / waiting / pending / skipped
    runs: int  # כמה פעמים רץ (יותר מ-1 = סבב נוסף או לולאת דחייה עם הערה)


def _next_node(last: str, round_no: int) -> str | None:
    """הצומת שרץ אחרי last, כשאין תשובה של המשתמשת באמצע."""
    if last == "next_round":
        return "find_suspects"
    if last == "find_suspects" and round_no > 1:
        return "propose_fix"  # בסבב 2 ומעלה אין baseline
    names = [n for n, _, _ in ORDER]
    if last == "finish":
        return None
    return names[names.index(last) + 1]


def flow_states(visited: list[str], phase: str, heading: str | None = None) -> list[NodeState]:
    """phase: "running" (הגרף רץ), "review_fix" / "review_push" (מחכה), "done" / "error" (הסתיים), "idle".
    heading: כשממשיכים אחרי תשובה, הצומת שהגרף הולך אליו (heading_to)."""
    round_no = visited.count("next_round") + 1
    # מה רץ בסבב הנוכחי: מה שאחרי ה-next_round האחרון
    start = len(visited) - visited[::-1].index("next_round") if "next_round" in visited else 0
    this_round = set(visited[start:])
    ever = set(visited)
    last = visited[-1] if visited else None

    if phase in INTERRUPTS:
        active = phase
    elif phase == "running" and heading and last in INTERRUPTS:
        active = heading
    elif phase == "running":
        active = _next_node(last, round_no) if last else ORDER[0][0]
    else:
        active = None

    result = []
    for node, label, hint in ORDER:
        ran = node in this_round if node in ROUND_NODES else node in ever
        if node == active:
            state = "waiting" if phase in INTERRUPTS else "running"
        elif ran:
            state = "done"
        elif phase in ("done", "error"):
            state = "skipped"
        else:
            state = "pending"
        result.append(NodeState(node, label, hint, state, visited.count(node)))
    return result


def current_round(visited: list[str]) -> int:
    return visited.count("next_round") + 1
