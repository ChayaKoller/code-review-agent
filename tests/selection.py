"""בדיקות ל-core/selection.py. מריצים מהשורש: python -m pytest tests -v"""
from pathlib import Path

from core import config
from core.selection import select_files

BODY = "\n".join(f"x{i} = {i}" for i in range(10)) + "\n"  # 10 שורות


def write(root: Path, rel: str, text: str = BODY) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def make_tree(root: Path) -> None:
    write(root, "app.py")
    write(root, "utils/helpers.py")
    write(root, "tests/test_app.py")  # בתיקיית טסטים
    write(root, "test_root.py")  # תבנית test_*.py
    write(root, "root_test.py")  # תבנית *_test.py
    write(root, "conftest.py")
    write(root, "tiny.py", "x = 1\n")  # קצר מדי
    write(root, "big.py", "x = 1\n" * (config.MAX_FILE_LINES + 1))  # ארוך מדי
    write(root, "empty.py", "")  # ריק
    write(root, ".venv/lib.py")  # תיקייה שמתעלמים ממנה
    write(root, ".git/hooks.py")
    write(root, "README.md", "# hi\n")  # לא פייתון
    (root / "bad.py").write_bytes(b"\xff\xfe\x00 not utf8\n" * 10)


def test_selects_only_real_source_files(tmp_path):
    make_tree(tmp_path)
    sel = select_files(str(tmp_path))
    assert sel.selected == ["app.py", "utils/helpers.py"]


def test_skipped_files_have_reasons(tmp_path):
    make_tree(tmp_path)
    skipped = select_files(str(tmp_path)).skipped
    assert "טסט" in skipped["tests/test_app.py"]
    assert "טסט" in skipped["test_root.py"]
    assert "טסט" in skipped["root_test.py"]
    assert "טסט" in skipped["conftest.py"]
    assert "קצר" in skipped["tiny.py"]
    assert "קצר" in skipped["empty.py"]
    assert "ארוך" in skipped["big.py"]
    assert "קידוד" in skipped["bad.py"]


def test_ignored_dirs_and_non_python_are_not_even_listed(tmp_path):
    make_tree(tmp_path)
    sel = select_files(str(tmp_path))
    everything = set(sel.selected) | set(sel.skipped)
    assert ".venv/lib.py" not in everything
    assert ".git/hooks.py" not in everything
    assert "README.md" not in everything


def test_root_folder_named_tests_does_not_hide_everything(tmp_path):
    root = tmp_path / "tests" / "proj"
    write(root, "app.py")
    assert select_files(str(root)).selected == ["app.py"]


def test_paths_use_forward_slashes_and_are_sorted(tmp_path):
    write(tmp_path, "b/z.py")
    write(tmp_path, "a/y.py")
    write(tmp_path, "a/b/x.py")
    sel = select_files(str(tmp_path))
    assert sel.selected == ["a/b/x.py", "a/y.py", "b/z.py"]
    assert all("\\" not in p for p in sel.selected)


def test_does_not_modify_files(tmp_path):
    make_tree(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    select_files(str(tmp_path))
    after = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
