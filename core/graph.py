"""core/graph.py: בניית הגרף. רק חיבורים והחלטות ניתוב, הלוגיקה עצמה ב-nodes.py.

START -> prepare -> select -> find_suspects
  find_suspects --(אין חשדות)--> finish
  find_suspects --(יש)--> baseline -> propose_fix
  propose_fix --(אין תיקון)--> finish
  propose_fix --(יש)--> review_fix                       [interrupt 1: על ה-diff]
  review_fix --approve--> apply_fix -> verify_fix
  review_fix --reject_with_note (ונשארו ניסיונות)--> propose_fix   [הלולאה]
  review_fix --reject--> finish
  verify_fix --(יש .git)--> review_push                   [interrupt 2: על הדחיפה]
  verify_fix --(קובץ בודד)--> finish
  review_push --push--> publish -> finish
  review_push --no_push--> finish
finish -> END   (finish תמיד מוחק את ההעתק)
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from core import config, nodes
from core.nodes import Deps
from core.state import APPROVE, PUSH, REJECT_WITH_NOTE, ReviewState


def route_after_find(state: ReviewState) -> str:
    return "baseline" if state.get("suspects") else "finish"


def route_after_propose(state: ReviewState) -> str:
    return "review_fix" if state.get("fixes") else "finish"


def route_after_review(state: ReviewState) -> str:
    if state["decision"] == APPROVE:
        return "apply_fix"
    if state["decision"] == REJECT_WITH_NOTE and state["attempts"] < config.MAX_REJECT_ATTEMPTS:
        return "propose_fix"
    return "finish"


def route_after_verify(state: ReviewState) -> str:
    return "review_push" if state.get("is_git_repo") and state.get("commit_sha") else "finish"


def route_after_push_review(state: ReviewState) -> str:
    return "publish" if state["push_decision"] == PUSH else "finish"


_NODES = {
    "prepare": nodes.prepare,
    "select": nodes.select,
    "find_suspects": nodes.find_suspects,
    "baseline": nodes.baseline,
    "propose_fix": nodes.propose_fix,
    "review_fix": nodes.review_fix,
    "apply_fix": nodes.apply_fix,
    "verify_fix": nodes.verify_fix,
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
    builder.add_conditional_edges("find_suspects", route_after_find, ["baseline", "finish"])
    builder.add_edge("baseline", "propose_fix")
    builder.add_conditional_edges("propose_fix", route_after_propose, ["review_fix", "finish"])
    builder.add_conditional_edges(
        "review_fix", route_after_review, ["apply_fix", "propose_fix", "finish"]
    )
    builder.add_edge("apply_fix", "verify_fix")
    builder.add_conditional_edges("verify_fix", route_after_verify, ["review_push", "finish"])
    builder.add_conditional_edges("review_push", route_after_push_review, ["publish", "finish"])
    builder.add_edge("publish", "finish")
    builder.add_edge("finish", END)
    return builder.compile(checkpointer=checkpointer or MemorySaver())
