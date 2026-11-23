"""eval/run_eval.py: הרצת הסוכן על הדאטהסט ומדידה.

הסוכן רץ בלי עצירות: כל interrupt ראשון מקבל approve, וכל interrupt שני no_push.
בהערכה אף פעם לא דוחפים.

מריצים מהשורש:
    python -m eval.run_eval                        # מקומי: טבלה + קובץ תוצאות
    python -m eval.run_eval --langsmith --name before   # ניסוי ב-LangSmith
    python -m eval.run_eval --only library_tracker
    python -m eval.run_eval --dataset test         # test set: באגים אמיתיים (BugsInPy)

ב-test set כל פריט הוא ריפו אמיתי בנקודת הבאג: eval/snapshot.py מכין העתק מקומי
(clone פעם אחת ל-eval/.cache), והסוכן סוקר רק את הקבצים ב-include (הקובץ הבאגי + 3 קבצים נקיים).
לכל פריט גרסת Python משלו (python) וחבילות נוספות (extra_packages), כי אלה ריפוזיטוריז ישנים.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from langgraph.types import Command

from core import config
from core.docker_runner import DockerRunner
from core.graph import build_graph
from core.llm import Reviewer
from core.nodes import Deps
from core.sandbox import _rmtree, remove_sandbox
from core.state import APPROVE, NO_PUSH
from eval.metrics import score
from eval.snapshot import prepare_snapshot

DATASETS_DIR = Path(__file__).parent / "datasets"
RESULTS_DIR = Path(__file__).parent / "results"
LANGSMITH_DATASETS = {"dev": "cra-dev", "test": "cra-test"}
_AUTO_ANSWERS = {"review_fix": {"decision": APPROVE}, "review_push": {"decision": NO_PUSH}}
_KEEP = ("suspects", "fixes", "verification", "verify_ok", "failing_tests", "failed_after",
         "new_failures", "skipped", "result")  # fmt: skip


def load_dataset(only: str | None = None, name: str = "dev") -> list[dict]:
    items = json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return [x for x in items if only is None or x["id"] == only]


@contextmanager
def python_version(version: str | None):
    """גרסת Python של image הבסיס לפריט אחד (ריפוזיטוריז ישנים לא רצים על 3.12). None = ברירת המחדל."""
    old = config.DOCKER_PYTHON_VERSION
    if version:
        config.DOCKER_PYTHON_VERSION = version
    try:
        yield
    finally:
        config.DOCKER_PYTHON_VERSION = old


def run_agent(repo: str, reviewer=None, runner=None, include=None, extra_packages=None) -> dict:
    """מריץ את הגרף על ריפו אחד עד הסוף ומחזיר את התוצאה, זמן וטוקנים."""
    reviewer = reviewer or Reviewer()
    deps = Deps(reviewer=reviewer, extra_packages=extra_packages)
    if runner is not None:
        deps.runner = runner
    graph = build_graph(deps)
    cfg = {"configurable": {"thread_id": uuid.uuid4().hex}}
    start = time.time()
    inputs = {"repo_path": repo, **({"include": include} if include else {})}
    try:
        state = graph.invoke(inputs, cfg)
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


def run_item(item: dict, reviewer=None, runner=None) -> dict:
    """פריט אחד מהדאטהסט. פריט עם fixed_commit (test set) רץ על העתק מקומי בנקודת הבאג."""
    if "fixed_commit" not in item:
        return run_agent(item["repo"], reviewer=reviewer, runner=runner, include=item.get("include"))
    tmp = Path(tempfile.mkdtemp(prefix="cra_eval_"))
    try:
        with python_version(item.get("python")):
            snap = prepare_snapshot(item, tmp / item["id"])
            if runner is None and item.get("python"):
                runner = DockerRunner(image=f"cra-runner:py{item['python']}")
            return run_agent(
                str(snap), reviewer=reviewer, runner=runner,
                include=item.get("include"), extra_packages=item.get("extra_packages"),
            )  # fmt: skip
    finally:
        _rmtree(tmp)  # כמו ב-sandbox: מטפל בקבצי קריאה-בלבד של .git ב-Windows


# ------------------------------------------------------------------ מקומי


def _fmt(x) -> str:
    return "-" if x is None else (f"{x:.2f}" if isinstance(x, float) else str(x))


def run_local(items: list[dict], name: str, dataset: str = "dev") -> Path:
    rows = []
    for item in items:
        print(f"מריצה על {item['id']}...")
        result = run_item(item)
        metrics = score(result, item["bugs"])
        rows.append({"id": item["id"], "metrics": metrics, "result": result})
        print(
            f"  recall {_fmt(metrics['recall'])} (מדויק {_fmt(metrics['recall_exact'])}) | "
            f"precision {_fmt(metrics['precision'])} (מדויק {_fmt(metrics['precision_exact'])}) | "
            f"תיקון {_fmt(metrics['fix_rate'])} | {result['seconds']}s | "
            f"{result['input_tokens']}+{result['output_tokens']} טוקנים"
        )
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}_{dataset}_{config.MODEL_NAME}.json"
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


def run_langsmith(name: str, dataset: str = "dev") -> None:
    """הדאטהסט נוצר ב-LangSmith בפעם הראשונה מ-<dataset>.json. אם משנים את הקובץ, למחוק אותו שם (או לשנות שם)."""
    from langsmith import Client, evaluate

    client = Client()
    ls_name = LANGSMITH_DATASETS[dataset]
    items = load_dataset(name=dataset)
    if not client.has_dataset(dataset_name=ls_name):
        ds = client.create_dataset(ls_name, description=f"code-review-agent {dataset} set")
        client.create_examples(
            dataset_id=ds.id,
            # inputs = כל הפריט חוץ מהתשובות (bugs), כדי ש-run_item יוכל להכין את ההעתק
            inputs=[{k: v for k, v in x.items() if k != "bugs"} for x in items],
            outputs=[{"id": x["id"], "bugs": x["bugs"]} for x in items],
        )
    evaluate(
        run_item,
        data=ls_name,
        evaluators=[_metric(k) for k in ("recall", "precision", "recall_exact", "precision_exact", "fix_rate")]
        + [_cost],
        experiment_prefix=f"{name}-{dataset}-{config.MODEL_NAME}",
        max_concurrency=1,  # אחד אחרי השני: מגבלת בקשות בשכבה החינמית
        metadata={"model": config.MODEL_NAME, "dataset": dataset},
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="before", help="שם הניסוי, למשל before / after")
    parser.add_argument("--only", help="רק ריפו אחד (לפי id). רק במצב מקומי")
    parser.add_argument("--langsmith", action="store_true", help="להריץ כניסוי ב-LangSmith")
    parser.add_argument("--dataset", choices=sorted(LANGSMITH_DATASETS), default="dev", help="dev או test")
    args = parser.parse_args(argv)
    items = load_dataset(args.only, args.dataset)
    if args.langsmith:
        run_langsmith(args.name, args.dataset)
    else:
        print("נשמר:", run_local(items, args.name, args.dataset))
    return 0


if __name__ == "__main__":
    sys.exit(main())
