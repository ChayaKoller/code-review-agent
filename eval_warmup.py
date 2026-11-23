import json
import os
import sys
import time
from pathlib import Path

from toy_graph import load_file, find_suspects

TOLERANCE = 3  # "מצא" = אותו קובץ, עד 3 שורות מהשורה האמיתית


def main(truth_path: str, repo_dir: str, delay: float = 6.0) -> None:
    truth = json.loads(Path(truth_path).read_text(encoding="utf-8"))
    rows = []

    for bug in truth:
        state = {"file_path": str(Path(repo_dir) / bug["file"])}
        state.update(load_file(state))

        start = time.time()
        try:
            suspects = find_suspects(state)["suspects"]
            error = None
        except Exception as e:
            suspects, error = [], str(e)[:80]
        seconds = round(time.time() - start, 1)

        hits = [s for s in suspects if abs(s - bug["line"]) <= TOLERANCE]
        rows.append({
            "id": bug["id"], "true_line": bug["line"],
            "suspects": suspects, "found": bool(hits),
            "correct": len(hits), "total": len(suspects),
            "seconds": seconds, "error": error,
        })
        print(f'{bug["id"]}: אמת={bug["line"]} מודל={suspects} '
              f'{"✓" if hits else "✗"} {seconds}s {error or ""}')
        time.sleep(delay)  # לא לעבור את מגבלת הבקשות לדקה

    found = sum(r["found"] for r in rows)
    correct = sum(r["correct"] for r in rows)
    total = sum(r["total"] for r in rows)
    print(f"\nrecall:    {found}/{len(rows)}")
    print(f"precision: {correct}/{total}" if total else "precision: אין חשודים")

    out = f'results_{os.environ["MODEL_NAME"]}.json'
    Path(out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print("נשמר:", out)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])