"""生成期自检：补丁先在临时 worktree 上试打，失败则按 git 指出的行号回读真实内容再重试。

背景（2026-10-18 评测）：apply 失败的主因不是 hunk 行数算错（--recount 早已兜住），
而是模型在它没被提供过的行区间凭记忆写上下文。放大检索窗口无效，只能回读真实文件。
"""
import subprocess

import pytest

from patch_generator import LLMPatchGenerator
from schemas import CodeContext, PatchGenerationContext, ProposedChange
from worktree_service import probe_apply

_GOOD = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def a():\n-    return 1\n+    return 2\n"
# 上下文里的 "return 9" 在文件里不存在 → 四档降级链全失败，且能报出 app.py:2
_BAD = "--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,2 @@\n def a():\n-    return 9\n+    return 2\n"


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


def _ctx(git_root=""):
    return PatchGenerationContext(
        task_id=7,
        repository_id=1,
        plan_id=1,
        issue_title="a() 返回值不对",
        issue_body="a() 应返回 2。",
        plan_summary="改 app.py 的返回值",
        proposed_changes=[ProposedChange(path="app.py", description="返回 2")],
        code_context=[CodeContext(path="app.py", line_start=1, line_end=2,
                                  matched_line=2, snippet="def a():\n    return 1\n")],
        base_commit=None,
        patch_revision=1,
        reviewer_feedback=None,
        git_root=str(git_root),
    )


def test_probe_apply_ok_leaves_no_temp_worktree(tmp_path):
    root = _repo(tmp_path)
    result = probe_apply(str(root), None, _GOOD, tag="t-ok")
    assert result["ok"] is True
    assert result["mode"]
    probes = root / ".codeforge" / "probes"
    assert not probes.exists() or not list(probes.iterdir())


def test_probe_apply_failure_carries_real_file_lines(tmp_path):
    root = _repo(tmp_path)
    result = probe_apply(str(root), None, _BAD, tag="t-bad")
    assert result["ok"] is False
    # 报错要能指出文件与行号，否则回喂没有定位价值
    assert "app.py" in result["error"]
    # hint 要带上真实文件内容，模型才有可逐字复制的素材
    assert "return 1" in result["hint"]
    assert "app.py" in result["hint"]


@pytest.mark.asyncio
async def test_generator_retries_with_real_context_after_probe_failure(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    gen = LLMPatchGenerator(api_key="test-key", max_retries=1)
    replies = [_BAD, _GOOD]
    prompts: list[str] = []

    async def fake(messages):
        prompts.append(messages[-1]["content"])
        return replies.pop(0)

    monkeypatch.setattr(gen, "_call", fake)
    res = await gen.generate(_ctx(root))

    assert res.provider == "deepseek"
    assert "+    return 2" in res.diff
    # 第一次 Prompt 不带重试段，第二次才带（第一次的行为与改造前逐字一致）
    assert "# Previous attempt failed" not in prompts[0]
    assert "# Previous attempt failed" in prompts[1]
    # 重试素材里必须有真实文件内容，而不只是一句 "patch does not apply"
    assert "return 1" in prompts[1]
    assert res.risk_notes and "无法应用" in res.risk_notes[0]


@pytest.mark.asyncio
async def test_generator_does_not_retry_when_patch_applies(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    gen = LLMPatchGenerator(api_key="test-key", max_retries=2)
    calls = []

    async def fake(messages):
        calls.append(1)
        return _GOOD

    monkeypatch.setattr(gen, "_call", fake)
    res = await gen.generate(_ctx(root))
    assert len(calls) == 1
    assert res.risk_notes == []


@pytest.mark.asyncio
async def test_self_check_exhausted_returns_model_patch_not_fake(tmp_path, monkeypatch):
    """自检连不过也不能退成 Fake。

    Fake 补丁会被 judge 判成 generator_fallback 整条剔除，等于把「补丁打不上」
    改写成「模型没产出」，可判定池会凭空缩水（2026-10-18 实测一轮从 17 条掉到 6 条）。
    """
    root = _repo(tmp_path)
    gen = LLMPatchGenerator(api_key="test-key", max_retries=1)

    async def always_bad(messages):
        return _BAD

    monkeypatch.setattr(gen, "_call", always_bad)
    res = await gen.generate(_ctx(root))

    assert res.provider == "deepseek"
    assert res.diff.strip()
    assert any("自检" in note for note in res.risk_notes)


@pytest.mark.asyncio
async def test_self_check_skipped_without_git_root(monkeypatch):
    """没有 git_root（测试环境 / Fake 生成器）时绝不能触发 worktree 操作。"""
    def boom(*args, **kwargs):
        raise AssertionError("git_root 为空时不应调用 probe_apply")

    monkeypatch.setattr("worktree_service.probe_apply", boom)
    gen = LLMPatchGenerator(api_key="test-key", max_retries=0)

    async def fake(messages):
        return _BAD

    monkeypatch.setattr(gen, "_call", fake)
    res = await gen.generate(_ctx())
    assert res.provider == "deepseek"
    assert res.risk_notes == []
