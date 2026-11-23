"""בדיקות ל-core/sandbox.py. מריצים מהשורש: python -m pytest tests -v"""
import hashlib
import os
import stat
from pathlib import Path

import pytest

from core import config
from core.sandbox import Sandbox, SandboxError, validate_source
from testkit import CLEAN_SOURCE, make_repo


def tree_hash(root: Path) -> str:
    """טביעת אצבע של כל הקבצים בתיקייה, כדי לוודא שהמקור לא השתנה."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


# ----------------------------------------------------------- validate_source


def test_validate_rejects_missing_path(tmp_path):
    with pytest.raises(SandboxError):
        validate_source(str(tmp_path / "nope"))


def test_validate_rejects_non_python_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    with pytest.raises(SandboxError, match=".py"):
        validate_source(str(f))


def test_validate_accepts_single_python_file(tmp_path):
    f = tmp_path / "script.py"
    f.write_text(CLEAN_SOURCE)
    assert validate_source(str(f)) == f.resolve()


def test_validate_rejects_folder_without_python(tmp_path):
    (tmp_path / "readme.md").write_text("hi")
    with pytest.raises(SandboxError):
        validate_source(str(tmp_path))


def test_validate_accepts_repo_without_tests(tmp_path):
    repo = make_repo(tmp_path, with_tests=False)
    assert validate_source(str(repo)) == repo.resolve()


def test_validate_accepts_repo_with_tests(tmp_path):
    repo = make_repo(tmp_path)
    assert validate_source(str(repo)) == repo.resolve()


# ------------------------------------------------------------- העתקה וניקוי


def test_copy_is_separate_and_includes_git_but_not_ignored_dirs(tmp_path):
    repo = make_repo(tmp_path)
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (repo / ".venv").mkdir()
    (repo / ".venv" / "junk.txt").write_text("x")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "app.cpython-313.pyc").write_bytes(b"x")

    with Sandbox(str(repo)) as sb:
        assert sb.path != repo
        assert (sb.path / "app.py").exists()
        assert (sb.path / ".git" / "HEAD").exists()  # .git מועתק
        assert sb.is_git_repo
        assert not (sb.path / ".venv").exists()
        assert not (sb.path / "__pycache__").exists()


def test_original_is_never_changed(tmp_path):
    repo = make_repo(tmp_path)
    before = tree_hash(repo)
    with Sandbox(str(repo)) as sb:
        (sb.path / "app.py").write_text("# שונה בהעתק\n", encoding="utf-8")
        (sb.path / "new_file.py").write_text("x = 1\n")
    assert tree_hash(repo) == before


def test_cleanup_after_normal_exit(tmp_path):
    repo = make_repo(tmp_path)
    with Sandbox(str(repo)) as sb:
        tmp_root = sb.path.parent
        assert tmp_root.exists()
    assert not tmp_root.exists()


def test_cleanup_after_exception(tmp_path):
    repo = make_repo(tmp_path)
    tmp_root = None
    with pytest.raises(RuntimeError):
        with Sandbox(str(repo)) as sb:
            tmp_root = sb.path.parent
            raise RuntimeError("משהו נכשל באמצע")
    assert tmp_root is not None and not tmp_root.exists()


def test_cleanup_removes_readonly_files(tmp_path):
    """מדמה את .git ב-Windows: קובץ לקריאה בלבד בתוך ההעתק."""
    repo = make_repo(tmp_path)
    with Sandbox(str(repo)) as sb:
        tmp_root = sb.path.parent
        ro = sb.path / ".git_objects_fake"
        ro.write_text("x")
        os.chmod(ro, stat.S_IREAD)
    assert not tmp_root.exists()


# ---------------------------------------------------------- תיקיית אב (workspace)


def test_copy_is_created_inside_workspace_and_only_it_is_deleted(tmp_path):
    repo = make_repo(tmp_path / "src")
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "keep.txt").write_text("אסור למחוק")
    (ws / "other_dir").mkdir()
    (ws / "other_dir" / "a.txt").write_text("x")

    with Sandbox(str(repo), workspace_dir=str(ws)) as sb:
        assert sb.path.parent.parent == ws.resolve()
        assert sb.path.parent.name.startswith(config.SANDBOX_PREFIX)
        assert (sb.path / "app.py").exists()
        tmp_root = sb.path.parent

    assert not tmp_root.exists()  # התת-תיקייה שלנו נמחקה
    assert (ws / "keep.txt").read_text() == "אסור למחוק"  # כל השאר נשאר
    assert (ws / "other_dir" / "a.txt").exists()
    assert [p.name for p in ws.iterdir() if p.name.startswith(config.SANDBOX_PREFIX)] == []


def test_workspace_must_exist(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(SandboxError, match="תיקיית האב"):
        Sandbox(str(repo), workspace_dir=str(tmp_path / "missing"))


def test_workspace_cannot_be_the_repo_or_inside_it(tmp_path):
    repo = make_repo(tmp_path)
    with pytest.raises(SandboxError):
        Sandbox(str(repo), workspace_dir=str(repo))
    inner = repo / "tests"
    with pytest.raises(SandboxError):
        Sandbox(str(repo), workspace_dir=str(inner))


def test_workspace_can_be_the_parent_of_the_repo(tmp_path):
    repo = make_repo(tmp_path)
    with Sandbox(str(repo), workspace_dir=str(tmp_path)) as sb:
        assert (sb.path / "app.py").exists()
        assert sb.path != repo
    assert (repo / "app.py").exists()  # המקור לא נפגע


def test_workspace_from_config(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "src")
    ws = tmp_path / "work"
    ws.mkdir()
    monkeypatch.setattr(config, "WORKSPACE_DIR", str(ws))
    with Sandbox(str(repo)) as sb:
        assert sb.path.parent.parent == ws.resolve()


def test_cleanup_refuses_to_delete_a_folder_it_did_not_create(tmp_path):
    repo = make_repo(tmp_path / "src")
    ws = tmp_path / "work"
    ws.mkdir()
    (ws / "keep.txt").write_text("x")
    sb = Sandbox(str(repo), workspace_dir=str(ws))
    sb._tmp = ws.resolve()  # מדמה באג: מצביעים על תיקיית האב
    with pytest.raises(RuntimeError, match="בטיחות"):
        sb.cleanup()
    assert (ws / "keep.txt").exists()


# ------------------------------------------------------- קובץ בודד ו-has_tests


def test_single_file_is_copied_alone(tmp_path):
    f = tmp_path / "script.py"
    f.write_text(CLEAN_SOURCE, encoding="utf-8")
    (tmp_path / "other.py").write_text("x = 1\n")
    before = f.read_bytes()

    with Sandbox(str(f)) as sb:
        assert sb.is_single_file
        assert sorted(p.name for p in sb.path.iterdir()) == ["script.py"]
        assert not sb.has_tests
        assert not sb.is_git_repo
        (sb.path / "script.py").write_text("# שונה\n", encoding="utf-8")
    assert f.read_bytes() == before


def test_single_file_named_like_a_test_is_not_treated_as_tests(tmp_path):
    f = tmp_path / "test_thing.py"
    f.write_text(CLEAN_SOURCE)
    with Sandbox(str(f)) as sb:
        assert sb.has_tests is False


def test_has_tests_reflects_the_copy(tmp_path):
    with Sandbox(str(make_repo(tmp_path / "a"))) as sb:
        assert sb.has_tests is True
    with Sandbox(str(make_repo(tmp_path / "b", with_tests=False))) as sb:
        assert sb.has_tests is False


def test_config_values_exist():
    assert ".git" not in config.COPY_IGNORE_DIRS
    assert config.TEST_TIMEOUT_SECONDS > 0
