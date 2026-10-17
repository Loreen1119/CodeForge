import subprocess

import pytest

from worktree_service import WorktreeError, apply_patch, create_worktree, remove_worktree


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@e.c"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "app.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "base"], check=True)
    return root


def test_apply_patch_failure_carries_git_stderr_by_mode(tmp_path):
    root = _repo(tmp_path)
    wt = create_worktree(str(root), 1, None)
    try:
        # 上下文完全对不上：四档降级链全部失败
        bad = "--- a/app.py\n+++ b/app.py\n@@ -9,9 +9,9 @@\n-def gone():\n+def gone():\n"
        with pytest.raises(WorktreeError) as exc:
            apply_patch(wt, bad)
        # 主消息仍是最保守档（strict）的 git 原始 stderr，str(exc) 语义不变
        assert "app.py" in str(exc.value)
        # 第二个 args 是结构化的分档 stderr，供调用方回喂模型重试
        detail = exc.value.args[1] if len(exc.value.args) > 1 else None
        assert isinstance(detail, dict)
        assert detail.get("git_stderr_by_mode") is not None
        by_mode = detail["git_stderr_by_mode"]
        assert set(by_mode) == {"strict", "recount", "relaxed", "recount+relaxed"}
        # 四档都失败了，每一档都该有非空 stderr（git 一定报 "patch does not apply" 之类）
        assert all(by_mode[m] for m in by_mode)
    finally:
        remove_worktree(str(root), 1)


def test_apply_patch_success_returns_mode_without_error_detail(tmp_path):
    root = _repo(tmp_path)
    wt = create_worktree(str(root), 1, None)
    try:
        good = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def a():\n-    return 1\n+    return 2\n"
        assert apply_patch(wt, good) == "strict"
    finally:
        remove_worktree(str(root), 1)
