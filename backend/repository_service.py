"""Read-only inspection and boundary checks for configured repositories."""
from __future__ import annotations

import subprocess
from pathlib import Path


class RepositoryValidationError(ValueError):
    pass


def _git(path: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RepositoryValidationError("无法执行 Git 检查") from exc
    if result.returncode != 0:
        raise RepositoryValidationError(result.stderr.strip() or "不是有效的 Git 仓库")
    return result.stdout.strip()


def inspect_repository(local_path: str, allowed_source_roots: list[str] | None = None) -> dict:
    configured = Path(local_path).expanduser()
    if not configured.exists():
        raise RepositoryValidationError("仓库路径不存在")
    if not configured.is_dir():
        raise RepositoryValidationError("仓库路径必须是目录")

    path = configured.resolve()
    git_root = Path(_git(path, "rev-parse", "--show-toplevel")).resolve()
    try:
        path.relative_to(git_root)
    except ValueError as exc:
        raise RepositoryValidationError("路径不在 Git 仓库范围内") from exc
    if path != git_root:
        raise RepositoryValidationError("仓库路径必须是 Git 仓库根目录")

    roots = allowed_source_roots or []
    if not roots:
        raise RepositoryValidationError("必须显式配置至少一个允许源码目录")
    resolved_roots: list[str] = []
    for root in roots:
        candidate = (git_root / root).resolve() if not Path(root).is_absolute() else Path(root).resolve()
        try:
            candidate.relative_to(git_root)
        except ValueError as exc:
            raise RepositoryValidationError("允许源码目录超出 Git 仓库范围") from exc
        if candidate == git_root:
            raise RepositoryValidationError("不允许将仓库根目录作为允许源码目录")
        if not candidate.exists() or not candidate.is_dir():
            raise RepositoryValidationError("允许源码目录不存在或不是目录")
        resolved_roots.append(str(candidate))

    branch = _git(path, "branch", "--show-current")
    default_branch = branch
    if _git(path, "remote"):
        try:
            default_branch = _git(path, "symbolic-ref", "refs/remotes/origin/HEAD").removeprefix("refs/remotes/origin/")
        except RepositoryValidationError:
            default_branch = branch
    status = _git(path, "status", "--porcelain=v1", "--untracked-files=all")
    try:
        base_commit = _git(path, "rev-parse", "HEAD")
    except RepositoryValidationError:
        base_commit = None
    return {
        "local_path": str(path),
        "git_root": str(git_root),
        "current_branch": branch or None,
        "default_branch": default_branch or branch or None,
        "is_dirty": bool(status),
        "base_commit": base_commit,
        "allowed_source_roots": resolved_roots,
    }
