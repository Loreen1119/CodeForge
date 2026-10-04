import subprocess

import pytest

from repository_service import RepositoryValidationError, inspect_repository


def make_git_repo(path):
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    (path / "src").mkdir()
    (path / "src" / "app.py").write_text("print('ok')\n", encoding="utf-8")


def test_inspect_git_repository_and_dirty_state(tmp_path):
    make_git_repo(tmp_path / "repo")
    info = inspect_repository(str(tmp_path / "repo"), ["src"])
    assert info["git_root"] == str((tmp_path / "repo").resolve())
    assert info["current_branch"] in ("master", "main", None)
    assert info["is_dirty"] is True
    assert info["allowed_source_roots"] == [str((tmp_path / "repo" / "src").resolve())]


@pytest.mark.parametrize("kind", ["missing", "file", "non_git"])
def test_rejects_invalid_repository_paths(tmp_path, kind):
    if kind == "missing":
        path = tmp_path / "missing"
    elif kind == "file":
        path = tmp_path / "file"
        path.write_text("not a repo", encoding="utf-8")
    else:
        path = tmp_path / "directory"
        path.mkdir()
    with pytest.raises(RepositoryValidationError):
        inspect_repository(str(path))


def test_rejects_allowed_root_outside_repository(tmp_path):
    repo = tmp_path / "repo"
    make_git_repo(repo)
    with pytest.raises(RepositoryValidationError):
        inspect_repository(str(repo), [".."])


def test_rejects_empty_or_root_source_allowlist(tmp_path):
    repo = tmp_path / "repo"
    make_git_repo(repo)
    with pytest.raises(RepositoryValidationError):
        inspect_repository(str(repo), [])
    with pytest.raises(RepositoryValidationError):
        inspect_repository(str(repo), ["."])
