"""ui/session.py: הגשר בין הממשק לגרף. אין כאן import של streamlit.

הבעיה ש-AgentSession פותר: Streamlit מריץ את כל הסקריפט מחדש בכל לחיצה (rerun).
הגרף, ה-checkpointer (שבו שמור ה-State בזמן interrupt) וה-thread_id חייבים לשרוד בין
ההרצות, אחרת אי אפשר להמשיך מנקודת העצירה. לכן הם נשמרים כאן, באובייקט אחד,
והממשק שומר את האובייקט ב-st.session_state.

כל פעולה (start / answer) מריצה את הגרף עד שהוא נעצר, ומחזירה Step:
  kind = "review_fix"  -> מחכה לאישור הראשון (info = מה שהצומת review_fix שלח ל-interrupt)
  kind = "review_push" -> מחכה לאישור השני
  kind = "done"        -> הגרף הגיע ל-END
  kind = "error"       -> משהו נכשל באמצע (למשל המודל לא זמין). ההעתק נמחק.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from langgraph.types import Command

from core.graph import build_graph
from core.llm import LLMUnavailable
from core.nodes import Deps
from core.sandbox import remove_sandbox

WAITING = ("review_fix", "review_push")


@dataclass
class Step:
    kind: str  # review_fix / review_push / done / error
    info: dict = field(default_factory=dict)  # מה שה-interrupt שלח (ריק ב-done/error)
    values: dict = field(default_factory=dict)  # ה-State המלא כרגע
    error: str = ""


class AgentSession:
    """ריצה אחת של הסוכן על מקור אחד. ריצה חדשה = אובייקט חדש."""

    def __init__(self, deps: Deps):
        self.deps = deps
        self.graph = build_graph(deps)  # עם MemorySaver: ה-State נשמר בזיכרון של התהליך
        self.config = {"configurable": {"thread_id": uuid.uuid4().hex}}
        self.history: list[dict] = []  # החלטות המשתמשת, לפי הסדר (להצגה)
        self.visited: list[str] = []  # הצמתים שרצו, לפי הסדר (למפת התהליך בצד המסך)
        self.step: Step | None = None

    # ------------------------------------------------------------ פעולות

    def start(self, source: str, include=None, exclude=None, on_node: Callable[[str], None] | None = None) -> Step:
        state = {"repo_path": source, "include": include or [], "exclude": exclude or []}
        return self._run(state, on_node)

    def answer(self, answer: dict, on_node: Callable[[str], None] | None = None) -> Step:
        """ממשיך מה-interrupt עם התשובה של המשתמשת (אותו מבנה כמו ב-CLI)."""
        if not self.step or self.step.kind not in WAITING:
            raise RuntimeError("הסוכן לא מחכה לתשובה כרגע")
        self.history.append({"at": self.step.kind, **answer})
        return self._run(Command(resume=answer), on_node)

    def cancel(self) -> None:
        """המשתמשת עזבה באמצע (ריצה חדשה / ביטול): finish לא ירוץ, לכן מוחקים את ההעתק כאן."""
        self.cleanup()
        self.step = Step("done", values=self.values, error="")

    # ------------------------------------------------------------ מצב

    @property
    def values(self) -> dict:
        return dict(self.graph.get_state(self.config).values)

    def cleanup(self) -> None:
        work_dir = self.values.get("work_dir")
        if work_dir and Path(work_dir).exists():
            remove_sandbox(work_dir)

    # ------------------------------------------------------------ פנימי

    def _run(self, graph_input, on_node) -> Step:
        try:
            # stream במקום invoke: אחרי כל צומת מקבלים עדכון, והממשק מציג איפה הסוכן נמצא
            for chunk in self.graph.stream(graph_input, self.config, stream_mode="updates"):
                for node in chunk:
                    if node == "__interrupt__":
                        continue
                    self.visited.append(node)
                    if on_node:
                        on_node(node)
        except LLMUnavailable as exc:
            return self._fail(str(exc))
        except Exception as exc:  # noqa: BLE001 - כל שגיאה מוצגת בממשק, וההעתק נמחק
            return self._fail(f"{type(exc).__name__}: {exc}")

        snapshot = self.graph.get_state(self.config)
        if snapshot.interrupts:  # הגרף נעצר ב-interrupt ומחכה
            info = snapshot.interrupts[0].value
            self.step = Step(info["kind"], info=info, values=dict(snapshot.values))
        else:
            self.step = Step("done", values=dict(snapshot.values))
        return self.step

    def _fail(self, message: str) -> Step:
        self.cleanup()
        self.step = Step("error", values=self.values, error=message)
        return self.step


def make_deps(workspace: str | None = None, extra_packages: list[str] | None = None) -> Deps:
    """Deps אמיתיים (Gemini + Docker). בבדיקות מחליפים את הפונקציה הזו במודל ו-Docker מדומים."""
    from core.llm import Reviewer

    return Deps(reviewer=Reviewer(), workspace_dir=workspace or None, extra_packages=extra_packages or None)
