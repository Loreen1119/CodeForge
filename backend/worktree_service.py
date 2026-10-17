"""Controlled git worktree lifecycle and patch application."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# `git worktree remove --force` 要删整棵工作树，大仓（三千+ 文件）在 Windows 上
# 实测跑不完 30s 的默认超时，于是删除超时 → 残留目录 → 同一任务的再次应用
# 会因「目录已存在」永久失败（前端「复用重试」也走这条路径）。
# 其余 git 操作保持 30s 不动，只给删除单独放宽。
REMOVE_TIMEOUT_S = int(os.environ.get("CODEFORGE_WORKTREE_REMOVE_TIMEOUT", "300"))


class WorktreeError(ValueError):
    pass


def _run(root: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError("Git Worktree 操作失败") from exc


def worktree_path(repository_root: str, task_id: int) -> Path:
    root = Path(repository_root).resolve()
    return root / ".codeforge" / "worktrees" / f"task-{task_id}"


def create_worktree(repository_root: str, task_id: int, base_commit: str | None = None, reuse: bool = False) -> Path:
    root = Path(repository_root).resolve()
    if not root.is_dir():
        raise WorktreeError("仓库目录不存在")
    discovered = _run(root, "rev-parse", "--show-toplevel")
    if discovered.returncode != 0 or Path(discovered.stdout.strip()).resolve() != root:
        raise WorktreeError("必须使用 Git 仓库根目录")
    target = worktree_path(str(root), task_id)
    if target.exists():
        if not reuse:
            raise WorktreeError("任务 Worktree 已存在")
        # 复用语义：残留先清掉再重建，保证每次 apply 都从干净的 base_commit 开始。
        # 否则一次失败 = 残留 = 之后每次都在这里 422，用户永远无法再试。
        remove_worktree(str(root), task_id)
    check = _run(root, "rev-parse", "--verify", (base_commit or "HEAD") + "^{commit}")
    if check.returncode != 0:
        raise WorktreeError("基准提交不存在")
    target.parent.mkdir(parents=True, exist_ok=True)
    revision = base_commit or "HEAD"
    result = _run(root, "worktree", "add", "--detach", str(target), revision)
    if result.returncode != 0:
        raise WorktreeError(result.stderr.strip() or "无法创建 Git Worktree")
    return target


def apply_patch(worktree: Path, diff: str) -> str:
    """把 diff 应用到 worktree，返回使用的模式（'strict' / 'recount' / 'relaxed' / 'recount+relaxed'）。

    三个坑：
    1. 必须用 bytes 喂 git。`text=True` + `input=str` 在 Windows 上会把 \\n 翻译成 \\r\\n，
       补丁每一行都多一个 CR，上下文行全部失配 —— 一份完全正确的补丁也会 100% 失败。
    2. 工作区行尾是 CRLF、补丁是 LF 时（Windows 默认 core.autocrlf=true），
       strict 模式会因为 CR 差异拒绝一份本来正确的补丁（实测）。
       这种情况下才降级用 --unidiff-zero 重试；降级事实由调用方写进事件，不静默。
    3. 模型给的 hunk 行数声明常与实际内容对不上（实测：`@@ -60,6 +66,7 @@` 声明 6 行、
       实际只有 5 行），git 直接报 `corrupt patch`。recount 是 git 官方为此提供的开关，
       只重算行数、**不放宽上下文精确匹配**，因此比 --unidiff-zero 保守，排在它前面。
    """
    if not worktree.is_dir() or not (worktree / ".git").exists():
        raise WorktreeError("Worktree 路径无效")
    payload = diff.encode("utf-8")

    def _run(*extra: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            ["git", "-C", str(worktree), "apply", *extra, "-"],
            input=payload, capture_output=True, timeout=30,
        )

    def _try(*extra: str) -> str:
        checked = _run("--check", *extra)
        if checked.returncode != 0:
            return checked.stderr.decode("utf-8", "replace").strip() or "Patch 无法应用"
        applied = _run(*extra)
        if applied.returncode != 0:
            return applied.stderr.decode("utf-8", "replace").strip() or "Patch 应用失败"
        return ""

    # 降级链：自保守到宽松，每档都先 --check 再真应用。降级事实由调用方落痕，不静默。
    modes: tuple[tuple[tuple[str, ...], str], ...] = (
        ((), "strict"),
        (("--recount",), "recount"),
        (("--unidiff-zero",), "relaxed"),
        (("--recount", "--unidiff-zero"), "recount+relaxed"),
    )
    errors: list[str] = []
    try:
        for extra, mode in modes:
            err = _try(*extra)
            if not err:
                return mode
            errors.append(err)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorktreeError("Patch 应用失败") from exc
    # 抛最保守档的错误：它最贴近补丁本身的毛病，诊断价值高于最宽松档的报错
    raise WorktreeError(errors[0] if errors else "Patch 无法应用")


def remove_worktree(repository_root: str, task_id: int) -> None:
    root = Path(repository_root).resolve()
    target = worktree_path(str(root), task_id)
    result = _run(root, "worktree", "remove", "--force", str(target), timeout=REMOVE_TIMEOUT_S)
    if result.returncode != 0 and target.exists():
        raise WorktreeError(result.stderr.strip() or "无法清理 Git Worktree")
    if target.exists():
        shutil.rmtree(target)
