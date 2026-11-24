"""eval/run_eval.py: הרצת הסוכן על הדאטהסט ומדידה.

הסוכן רץ בלי עצירות: כל interrupt ראשון מקבל approve, וכל interrupt שני no_push.
בהערכה אף פעם לא דוחפים.

מריצים מהשורש:
    python -m eval.run_eval                        # מקומי: טבלה + קובץ תוצאות
    python -m eval.run_eval --langsmith --name before   # ניסוי ב-LangSmith
    python -m eval.run_eval --only library_tracker
    python -m eval.run_eval --dataset test         # test set: באגים אמיתיים (BugsInPy)
    python -m eval.run_eval --dataset test --quick --name try1   # לולאת פיתוח מהירה: 3 פריטים
    python -m eval.run_eval --dataset test --ids thefuck_1,thefuck_25

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
# לולאת פיתוח מהירה (שלב 6): 3 פריטים שמייצגים את שלושת הכשלים שראינו ב-before:
# thefuck_1 = נמצא ותוקן לא נכון, thefuck_25 = נמצא ותיקון בקובץ נקי שבר טסט, thefuck_12 = לא נמצא.
# משפרים על אלה, ורק בסוף מריצים את כל ה-test (כדי לא "להתאים" את הסוכן לשלושה פריטים).
QUICK_IDS = {"test": ["thefuck_1", "thefuck_25", "thefuck_12"], "dev": ["library_tracker"]}
_KEEP = ("raw_suspects", "rejected_suspects", "screening", "suspects", "fixes", "verification", "verify_ok", "failing_tests", "initial_failing_tests", "failed_after",
         "new_failures", "skipped", "result")  # fmt: skip


def load_dataset(only: str | None = None, name: str = "dev", ids: list[str] | None = None) -> list[dict]:
    items = json.loads((DATASETS_DIR / f"{name}.json").read_text(encoding="utf-8"))
    wanted = set(ids or []) | ({only} if only else set())
    return [x for x in items if not wanted or x["id"] in wanted]


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


def results_path(name: str, dataset: str) -> Path:
    return RESULTS_DIR / f"{name}_{dataset}_{config.MODEL_NAME}.json"


def _save(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _mean(rows: list[dict], key: str) -> float | None:
    values = [r["metrics"][key] for r in rows if r["metrics"][key] is not None]
    return sum(values) / len(values) if values else None


def run_local(items: list[dict], name: str, dataset: str = "dev", resume: bool = False) -> Path:
    """הקובץ נשמר אחרי כל פריט. עם resume מדלגים על פריטים שכבר בקובץ (אותו שם, דאטהסט ומודל).
    אם פריט נופל (למשל המכסה של המודל נגמרה), עוצרים, ומה שרץ עד אז נשמר."""
    path = results_path(name, dataset)
    rows = json.loads(path.read_text(encoding="utf-8")) if resume and path.exists() else []
    done = {r["id"] for r in rows}
    for item in items:
        if item["id"] in done:
            print(f"דילוג על {item['id']} (כבר בקובץ)")
            continue
        print(f"מריצה על {item['id']}...")
        try:
            result = run_item(item)
        except Exception as e:  # noqa: BLE001 - שומרים את מה שרץ ומסבירים איך להמשיך
            print(f"  נעצר ב-{item['id']}: {e}")
            print(f"  להמשיך מכאן: python -m eval.run_eval --dataset {dataset} --name {name} --resume")
            break
        metrics = score(result, item["bugs"])
        rows.append({"id": item["id"], "metrics": metrics, "result": result})
        _save(path, rows)
        print(
            f"  recall {_fmt(metrics['recall'])} (מדויק {_fmt(metrics['recall_exact'])}) | "
            f"precision {_fmt(metrics['precision'])} (מדויק {_fmt(metrics['precision_exact'])}) | "
            f"תיקון {_fmt(metrics['fix_rate'])} | חשדות {metrics['raw_suspects']}->{metrics['suspects']} "
            f"(precision לפני סינון {_fmt(metrics['raw_precision'])}) | "
            f"נעצר ב: {', '.join(metrics['lost_at'].values())} | {result['seconds']}s | "
            f"{result['input_tokens']}+{result['output_tokens']} טוקנים"
        )
    if rows:
        funnel: dict[str, int] = {}
        for r in rows:
            for stage in (r["metrics"].get("lost_at") or {}).values():
                funnel[stage] = funnel.get(stage, 0) + 1
        print("משפך (איפה כל באג אמיתי נעצר):", ", ".join(f"{k} {v}" for k, v in sorted(funnel.items())))
        print(
            f"ממוצע על {len(rows)} פריטים: recall {_fmt(_mean(rows, 'recall'))} | "
            f"precision {_fmt(_mean(rows, 'precision'))} | תיקון {_fmt(_mean(rows, 'fix_rate'))} | "
            f"קריאות {sum(r['result'].get('llm_calls', 0) for r in rows)} | "
            f"טוקנים {sum(r['result'].get('input_tokens', 0) + r['result'].get('output_tokens', 0) for r in rows)}"
        )
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


def run_langsmith(name: str, dataset: str = "dev", from_results: str | None = None) -> None:
    """הדאטהסט נוצר ב-LangSmith בפעם הראשונה מ-<dataset>.json. אם משנים את הקובץ, למחוק אותו שם (או לשנות שם).

    from_results: קובץ תוצאות מקומי (למשל של ה-before). במקום להריץ את הסוכן, ה-target מחזיר את התוצאה
    השמורה של כל פריט, וה-evaluators מחשבים עליה את אותם מדדים. כך ה-before נכנס ל-LangSmith כניסוי
    על אותו דאטהסט, בלי להריץ שוב את הקוד הישן. רק פריטים שיש בקובץ נכנסים לניסוי."""
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
    target, data = run_item, ls_name
    if from_results:
        saved = {r["id"]: r["result"] for r in json.loads(Path(from_results).read_text(encoding="utf-8"))}
        target = lambda inputs: saved[inputs["id"]]  # noqa: E731 - תוצאה שמורה, בלי מודל ובלי Docker
        data = [e for e in client.list_examples(dataset_name=ls_name) if e.inputs.get("id") in saved]
    evaluate(
        target,
        data=data,
        evaluators=[_metric(k) for k in ("recall", "precision", "recall_exact", "precision_exact", "fix_rate",
                                         "raw_precision", "raw_recall")]
        + [_cost],
        experiment_prefix=f"{name}-{dataset}-{config.MODEL_NAME}",
        max_concurrency=1,  # אחד אחרי השני: מגבלת בקשות בשכבה החינמית
        metadata={"model": config.MODEL_NAME, "dataset": dataset, "replayed_from": from_results or ""},
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", default="before", help="שם הניסוי, למשל before / after")
    parser.add_argument("--only", help="רק ריפו אחד (לפי id). רק במצב מקומי")
    parser.add_argument("--ids", help="רשימת פריטים מופרדת בפסיקים, למשל thefuck_1,thefuck_25")
    parser.add_argument("--quick", action="store_true", help="רק 3 הפריטים של לולאת הפיתוח (QUICK_IDS)")
    parser.add_argument("--from-results", help="עם --langsmith: להעלות קובץ תוצאות מקומי כניסוי, בלי להריץ")
    parser.add_argument("--langsmith", action="store_true", help="להריץ כניסוי ב-LangSmith")
    parser.add_argument("--resume", action="store_true", help="להמשיך הרצה שנעצרה (מדלג על מה שכבר בקובץ)")
    parser.add_argument("--dataset", choices=sorted(LANGSMITH_DATASETS), default="dev", help="dev או test")
    parser.add_argument("--rounds", type=int, default=1,
                        help="מספר סבבים מקסימלי (v13). ברירת מחדל 1: בכל פריט יש באג אחד, וכך ההשוואה ל-before הוגנת")
    args = parser.parse_args(argv)
    config.MAX_ROUNDS = args.rounds
    ids = args.ids.split(",") if args.ids else (QUICK_IDS[args.dataset] if args.quick else None)
    items = load_dataset(args.only, args.dataset, ids)
    if args.langsmith:
        run_langsmith(args.name, args.dataset, args.from_results)
    else:
        print("נשמר:", run_local(items, args.name, args.dataset, args.resume))
    return 0


if __name__ == "__main__":
    sys.exit(main())
