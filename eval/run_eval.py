"""eval/run_eval.py: הרצת הסוכן על הדאטהסט ומדידה.

הסוכן רץ בלי עצירות: כל interrupt ראשון מקבל approve, וכל interrupt שני no_push.
בהערכה אף פעם לא דוחפים.

מריצים מהשורש:
    python -m eval.run_eval                        # מקומי: טבלה + קובץ תוצאות
    python -m eval.run_eval --langsmith --name before   # ניסוי ב-LangSmith
    python -m eval.run_eval --only library_tracker
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from langgraph.types import Command

from core import config
from core.graph import build_graph
from core.llm import Reviewer
from core.nodes import Deps
from core.sandbox import remove_sandbox
from core.state import APPROVE, NO_PUSH
from eval.metrics import score

DATASET_FILE = Path(__file__).parent / "datasets" / "dev.json"
RESULTS_DIR = Path(__file__).parent / "results"
LANGSMITH_DATASET = "cra-dev"
_AUTO_ANSWERS = {"review_fix": {"decision": APPROVE}, "review_push": {"decision": NO_PUSH}}
_KEEP = ("suspects", "fixes", "verification", "verify_ok", "failing_tests", "failed_after",
         "new_failures", "skipped", "result")  # fmt: skip


def load_dataset(only: str | None = None) -> list[dict]:
    items = json.loads(DATASET_FILE.read_text(encoding="utf-8"))
    return [x for x in items if only is None or x["id"] == only]


def run_agent(repo: str, reviewer=None, runner=None) -> dict:
    """מריץ את הגרף על ריפו אחד עד הסוף ומחזיר את התוצאה, זמן וטוקנים."""
    reviewer = reviewer or Reviewer()
    deps = Deps(reviewer=reviewer) if runner is None else Deps(reviewer=reviewer, runner=runner)
    graph = build_graph(deps)
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    start = time.time()
    try:
        state = graph.invoke({"repo_path": repo}, cfg)
        while "__interrupt__" in state:
            kind = state["__interrupt__"][0].value["kind"]
            state = graph.invoke(Command(resume=_AUTO_ANSWERS[kind]), cfg)
    finally:
        work_dir = graph.get_state(cfg).values.get("work_dir")
        if work_dir and Path(work_dir).exists():
            remove_sandbox(work_dir)
    out = {k: state.get(k) for k in _KEEP}
    out["seconds"] = round(time.time() - start, 1)
    out["llm_calls"] = getattr(reviewer, "calls", 0)
    out["input_tokens"] = getattr(reviewer, "input_tokens", 0)
    out["output_tokens"] = getattr(reviewer, "output_tokens", 0)
    return out


# ------------------------------------------------------------------ מקומי


def _fmt(x) -> str:
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def run_local(items: list[dict], name: str) -> Path:
    rows = []
    for item in items:
        print(f"מריצה על {item['id']}...")
        result = run_agent(item["repo"])
        metrics = score(result, item["bugs"])
        rows.append({"id": item["id"], "metrics": metrics, "result": result})
        print(
            f"  recall {_fmt(metrics['recall'])} (מדויק {_fmt(metrics['recall_exact'])}) | "
            f"precision {_fmt(metrics['precision'])} (מדויק {_fmt(metrics['precision_exact'])}) | "
            f"תיקון {_fmt(metrics['fix_rate'])} | {result['seconds']}s | "
            f"{result['input_tokens']}+{result['output_tokens']} טוקנים"
        )
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}_{config.MODEL_NAME}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# --------------------------------------------------------------- LangSmith


def _metric(key: str):
    """evaluator של LangSmith למדד אחד. ציון None (למשל fix_rate בלי טסטים) לא נספר בממוצע."""

    def evaluator(run, example) -> dict:
        value = score(run.outputs or {}, example.outputs["bugs"])[key]
        return {"key": key, "score": value}

    evaluator.__name__ = key
    return evaluator


def _cost(run, example) -> dict:
    out = run.outputs or {}
    return {"key": "tokens", "score": out.get("input_tokens", 0) + out.get("output_tokens", 0)}


def run_langsmith(name: str) -> None:
    """הדאטהסט נוצר ב-LangSmith בפעם הראשונה מ-dev.json. אם משנים את dev.json, למחוק אותו שם (או לשנות שם)."""
    from langsmith import Client, evaluate

    client = Client()
    if not client.has_dataset(dataset_name=LANGSMITH_DATASET):
        ds = client.create_dataset(LANGSMITH_DATASET, description="code-review-agent dev set")
        client.create_examples(
            dataset_id=ds.id,
            inputs=[{"repo": x["repo"]} for x in load_dataset()],
            outputs=[{"id": x["id"], "bugs": x["bugs"]} for x in load_dataset()],
        )
    evaluate(
        lambda inputs: run_agent(inputs["repo"]),
        data=LANGSMITH_DATASET,
        evaluators=[_metric(k) for k in ("recall", "precision", "recall_exact", "precision_exact", "fix_rate")]
        + [_cost],
        experiment_prefix=f"{name}-{config.MODEL_NAME}",
        max_concurrency=1,  # אחד אחרי השני: מגבלת בקשות בשכבה החינמית
        metadata={"model": config.MODEL_NAME},
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="before", help="שם הניסוי, למשל before / after")
    parser.add_argument("--only", help="רק ריפו אחד (לפי id). רק במצב מקומי")
    parser.add_argument("--langsmith", action="store_true", help="להריץ כניסוי ב-LangSmith")
    args = parser.parse_args(argv)
    items = load_dataset(args.only)
    if args.langsmith:
        run_langsmith(args.name)
    else:
        print("נשמר:", run_local(items, args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
