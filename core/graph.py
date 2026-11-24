"""core/graph.py: בניית הגרף. רק חיבורים והחלטות ניתוב, הלוגיקה עצמה ב-nodes.py.

START -> prepare -> select -> find_suspects
  find_suspects --(אין חשדות)--> finish
  find_suspects --(יש)--> baseline -> propose_fix
  propose_fix --(אין תיקון)--> finish
  propose_fix --(יש)--> screen_fixes                     [כל תיקון לבד מול הטסטים; שבר -> ניסיון שני]
  screen_fixes --(לא נשאר תיקון)--> finish
  screen_fixes --(נשאר)--> review_fix                    [interrupt 1: על ה-diff]
  review_fix --approve--> apply_fix -> verify_fix
  review_fix --reject_with_note (ונשארו ניסיונות)--> propose_fix   [הלולאה]
  review_fix --reject--> finish
  verify_fix --(נשארו טסטים שנכשלים, הסבב שיפר, לא נשבר כלום, round < MAX_ROUNDS)--> next_round -> find_suspects   [לולאת סבבים, v13]
  verify_fix --(יש .git)--> review_push                   [interrupt 2: על הדחיפה, פעם אחת עם כל הסבבים]
  verify_fix --(קובץ בודד)--> finish
  בסבב 2 ומעלה: כל מקום שהיה הולך ל-finish הולך ל-review_push, כי יש כבר תיקונים מסבבים קודמים בענף.
  בסבב 2 ומעלה: find_suspects -> propose_fix בלי baseline (תוצאת verify_fix היא ה-baseline החדש).
  review_push --push--> publish -> finish
  review_push --no_push--> finish
finish -> END   (finish תמיד מוחק את ההעתק)
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from core import config, nodes
from core.nodes import Deps
from core.state import APPROVE, PUSH, REJECT_WITH_NOTE, VERIFY_TESTS, ReviewState


def _end(state: ReviewState) -> str:
    """לאן הולכים כשהסבב הנוכחי נגמר בלי תיקון. בסבב הראשון: finish.
    בסבב 2 ומעלה כבר יש בענף תיקונים שאושרו, ולכן מציעים לדחוף אותם."""
    if state.get("round", 1) > 1 and state.get("is_git_repo") and state.get("commit_sha"):
        return "review_push"
    return "finish"


def route_after_find(state: ReviewState) -> str:
    if not (state.get("suspects") or state.get("doubtful_suspects")):
        return _end(state)
    return "baseline" if state.get("round", 1) == 1 else "propose_fix"


def route_after_propose(state: ReviewState) -> str:
    return "screen_fixes" if state.get("fixes") else _end(state)


def route_after_screen(state: ReviewState) -> str:
    return "review_fix" if state.get("fixes") else _end(state)


def route_after_review(state: ReviewState) -> str:
    if state["decision"] == APPROVE:
        return "apply_fix"
    if state["decision"] == REJECT_WITH_NOTE and state["attempts"] < config.MAX_REJECT_ATTEMPTS:
        return "propose_fix"
    return _end(state)


def should_continue(state: ReviewState) -> bool:
    """תנאי העצירה של לולאת הסבבים. ממשיכים רק אם כל אלה מתקיימים:
    יש טסטים שאפשר לסמוך עליהם, הבדיקה רצה בטסטים ולא נשבר כלום, עדיין נשארו טסטים שנכשלים,
    הסבב הזה הוריד את מספרם (אם לא שיפר, אין טעם להמשיך), ועוד לא הגענו ל-MAX_ROUNDS."""
    before = state.get("failing_tests", [])
    after = state.get("failed_after", [])
    return bool(
        state.get("has_tests")
        and not state.get("baseline_broken")
        and state.get("verification") == VERIFY_TESTS
        and not state.get("new_failures")
        and after
        and len(after) < len(before)
        and state.get("round", 1) < config.MAX_ROUNDS
    )


def route_after_verify(state: ReviewState) -> str:
    if should_continue(state):
        return "next_round"
    return "review_push" if state.get("is_git_repo") and state.get("commit_sha") else "finish"


def route_after_push_review(state: ReviewState) -> str:
    return "publish" if state["push_decision"] == PUSH else "finish"


_NODES = {
    "prepare": nodes.prepare,
    "select": nodes.select,
    "find_suspects": nodes.find_suspects,
    "baseline": nodes.baseline,
    "propose_fix": nodes.propose_fix,
    "screen_fixes": nodes.screen_fixes,
    "review_fix": nodes.review_fix,
    "apply_fix": nodes.apply_fix,
    "verify_fix": nodes.verify_fix,
    "next_round": nodes.next_round,
    "review_push": nodes.review_push,
    "publish": nodes.publish_fix,
    "finish": nodes.finish,
}


def build_graph(deps: Deps, checkpointer=None):
    """checkpointer שומר את ה-State כשהגרף נעצר ב-interrupt. ברירת מחדל: בזיכרון."""
    builder = StateGraph(ReviewState)
    for name, fn in _NODES.items():
        # כל צומת מקבל גם את deps. ה-lambda עם fn=fn כדי שכל צומת יקבל את הפונקציה שלו
        builder.add_node(name, lambda state, fn=fn: fn(state, deps))

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "select")
    builder.add_edge("select", "find_suspects")
    builder.add_conditional_edges(
        "find_suspects", route_after_find, ["baseline", "propose_fix", "review_push", "finish"]
    )
    builder.add_edge("baseline", "propose_fix")
    builder.add_conditional_edges("propose_fix", route_after_propose, ["screen_fixes", "review_push", "finish"])
    builder.add_conditional_edges("screen_fixes", route_after_screen, ["review_fix", "review_push", "finish"])
    builder.add_conditional_edges(
        "review_fix", route_after_review, ["apply_fix", "propose_fix", "review_push", "finish"]
    )
    builder.add_edge("apply_fix", "verify_fix")
    builder.add_conditional_edges("verify_fix", route_after_verify, ["next_round", "review_push", "finish"])
    builder.add_edge("next_round", "find_suspects")  # הלולאה: סבב חדש על הקוד המתוקן
    builder.add_conditional_edges("review_push", route_after_push_review, ["publish", "finish"])
    builder.add_edge("publish", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())
