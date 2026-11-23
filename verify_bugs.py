"""Check that every warm-up bug is a real fail-to-pass case.

For each bug in ground_truth.json:
  1. the buggy repo must FAIL the bug's test file,
  2. after applying the fix in a temporary copy, that test file must pass.
(In the warm-up repo every bug has its own file, so the other bugs do not interfere.)

Run from the project root:  python verify_bugs.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).parent / "warmup_repo"
BUGS = json.loads((REPO / "ground_truth.json").read_text(encoding="utf-8"))


def run_pytest(cwd, target=None):
    cmd = [sys.executable, "-m", "pytest", "-q", "-x", "--no-header", "-p", "no:cacheprovider"]
    if target:
        cmd.append(target)
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60)
    return result.returncode == 0


def main():
    all_ok = True
    for bug in BUGS:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(REPO, work)

            fails_before = not run_pytest(work, bug["failing_test"])

            src = work / bug["file"]
            text = src.read_text(encoding="utf-8")
            assert bug["buggy"] in text, f"{bug['id']}: buggy line not found"
            src.write_text(text.replace(bug["buggy"], bug["fixed"]), encoding="utf-8")
            # Only this bug's tests: the other warm-up bugs are still in the repo.
            passes_after = run_pytest(work, bug["failing_test"])

        ok = fails_before and passes_after
        all_ok &= ok
        print(f"{bug['id']}: fails_before={fails_before} passes_after={passes_after} -> {'OK' if ok else 'PROBLEM'}")

    print("ALL GOOD" if all_ok else "SOME BUGS ARE NOT VALID")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
