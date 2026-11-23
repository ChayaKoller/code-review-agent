import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel
import sys
import tempfile
from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt




load_dotenv()
class SuspectLines(BaseModel):
    lines: list[int]


class ReviewState(TypedDict):
    file_path: str
    source: str
    suspects: list[int]
    old_line: str
    new_line: str
    decision: str
    output_path: str
    rejection_note: str
    attempts: int


def load_file(state: ReviewState) -> dict:
    with open(state["file_path"], encoding="utf-8") as f:
        return {"source": f.read()}


def find_suspects(state: ReviewState) -> dict:
    numbered = "\n".join(
        f"{n}: {line}"
        for n, line in enumerate(state["source"].splitlines(), start=1)
    )
    model = ChatGoogleGenerativeAI(model=os.environ["MODEL_NAME"])
    structured = model.with_structured_output(SuspectLines)
    result = structured.invoke(
        "You are reviewing a Python file. Each line starts with its line number.\n"
        "Return the line numbers that contain a likely bug "
        "(off-by-one, missing None handling, or wrong logic). "
        "Return an empty list if there are none.\n\n" + numbered
    )
    return {"suspects": result.lines}


def propose_fix(state: ReviewState) -> dict:
    if state.get("rejection_note"):
        print("הערת הדחייה שהתקבלה:", state["rejection_note"])
    old = state["source"].splitlines()[state["suspects"][0] - 1]
    new = old.replace("page * page_size", "(page - 1) * page_size")
    return {
        "old_line": old,
        "new_line": new,
        "attempts": state.get("attempts", 0) + 1,
    }

def human_approval(state: ReviewState) -> dict:
    answer = interrupt({
        "line": state["suspects"][0],
        "old": state["old_line"],
        "new": state["new_line"],
    })
    return {
        "decision": answer["decision"],
        "rejection_note": answer.get("note", ""),
    }



def apply_fix(state: ReviewState) -> dict:
    lines = state["source"].splitlines()

    lines[state["suspects"][0] - 1] = state["new_line"]

    # תיקיית הפרויקט שבה נמצא הקובץ review.py
    project_dir = os.path.dirname(os.path.abspath(__file__))

    # יצירת תיקייה בתוך הפרויקט
    out_dir = os.path.join(project_dir, "fixed_files")
    os.makedirs(out_dir, exist_ok=True)

    # יצירת נתיב לקובץ החדש
    out_path = os.path.join(
        out_dir,
        os.path.basename(state["file_path"])
    )

    # כתיבת הקובץ המתוקן
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    return {"output_path": out_path}

def route_after_find(state: ReviewState) -> str:
    return "propose_fix" if state["suspects"] else END


def route_after_approval(state: ReviewState) -> str:
    if state["decision"] == "approve":
        return "apply_fix"
    if state["decision"] == "reject_with_note" and state["attempts"] < 2:
        return "propose_fix"
    return END


builder = StateGraph(ReviewState)
builder.add_node("load_file", load_file)
builder.add_node("find_suspects", find_suspects)
builder.add_node("propose_fix", propose_fix)
builder.add_node("human_approval", human_approval)
builder.add_node("apply_fix", apply_fix)
builder.add_edge(START, "load_file")
builder.add_edge("load_file", "find_suspects")
builder.add_conditional_edges("find_suspects", route_after_find)
builder.add_edge("propose_fix", "human_approval")
builder.add_conditional_edges("human_approval", route_after_approval)
builder.add_edge("apply_fix", END)

graph = builder.compile(checkpointer=MemorySaver())


if __name__ == "__main__":
    config = {"configurable": {"thread_id": "1"}}
    result = graph.invoke({"file_path": sys.argv[1]}, config)

    while "__interrupt__" in result:
        info = result["__interrupt__"][0].value
        print(f"שורה {info['line']}:")
        print("  לפני:", info["old"])
        print("  אחרי:", info["new"])
        choice = input("approve / reject / note? ").strip()
        if choice == "note":
            payload = {"decision": "reject_with_note", "note": input("הערה: ")}
        else:
            payload = {"decision": choice}
        result = graph.invoke(Command(resume=payload), config)

    print(result.get("output_path", "לא בוצע שינוי"))