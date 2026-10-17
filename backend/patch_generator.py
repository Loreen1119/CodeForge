"""Patch generation abstraction and implementations for testing / production.

- `FakePatchGenerator`: deterministic, no network. Used in tests and as the
  safe fallback when the LLM is unavailable or fails.
- `LLMPatchGenerator`: calls a DeepSeek-compatible OpenAI endpoint to produce a
  real unified diff. On failure it retries with the failure reason appended to
  the prompt, then falls back to `FakePatchGenerator` so the pipeline never
  breaks.
"""
from typing import Protocol
import os
import re
import json
import hashlib
import httpx
from schemas import PatchGenerationContext, PatchGenerationResult

class PatchGenerator(Protocol):
    """Protocol for patch generation."""
    async def generate(self, ctx: PatchGenerationContext) -> PatchGenerationResult: ...

class FakePatchGenerator:
    """Fake patch generator with configurable modes for testing."""

    def __init__(self, mode: str = 'valid'):
        """Initialize with mode: 'valid', 'unapplicable', 'out_of_scope', 'dangerous'."""
        assert mode in ('valid', 'unapplicable', 'out_of_scope', 'dangerous'), f"Unknown mode: {mode}"
        self.mode = mode

    async def generate(self, ctx: PatchGenerationContext) -> PatchGenerationResult:
        """Generate a fake patch based on mode."""
        plan_json = None
        if hasattr(ctx, 'plan_json'):
            plan_json = ctx.plan_json

        if self.mode == 'valid':
            return self._generate_valid(ctx, plan_json)
        elif self.mode == 'unapplicable':
            return self._generate_unapplicable(ctx)
        elif self.mode == 'out_of_scope':
            return self._generate_out_of_scope(ctx, plan_json)
        elif self.mode == 'dangerous':
            return self._generate_dangerous(ctx)

    def _generate_valid(self, ctx: PatchGenerationContext, plan_json) -> PatchGenerationResult:
        """用真实 code_context 片段构造可 apply 的 diff。

        必须是真实内容：伪造行能骗过 validate_patch（它不校验 hunk 计数、不读文件），
        但过不了 git apply --check —— 那会让"打通闭环"变成假的。
        """
        # Try to find a matching file with code_context
        target = None
        cc = None
        for pc in ctx.proposed_changes:
            for context in ctx.code_context:
                if context.path == pc.path:
                    target = pc.path
                    cc = context
                    break
            if target:
                break

        # If we have a matching target with code_context, use it
        if target is not None and cc is not None:
            lines = [line for line in cc.snippet.split('\n')] if cc.snippet else []
            if lines:
                marker = '# codeforge-fake-patch' if target.endswith('.py') else '// codeforge-fake-patch'
                body = ''.join(f' {line}\n' for line in lines)
                diff = (
                    f'--- a/{target}\n'
                    f'+++ b/{target}\n'
                    f'@@ -{cc.line_start},{len(lines)} +{cc.line_start},{len(lines) + 1} @@\n'
                    f'{body}'
                    f'+{marker}\n'
                )
                return PatchGenerationResult(
                    diff=diff,
                    rationale='fake(valid): 在 code_context 片段末尾追加一行注释标记',
                    changed_files=[target],
                    risk_notes=[],
                    provider='fake', model=None,
                    prompt_digest='fake-prompt-digest', response_digest='fake-response-digest',
                )

        # 没有可用的 code_context，就构造不出"真的能 apply"的 diff。
        #
        # 旧实现在这里返回 `@@ -1,0 +1,1 @@` 的零上下文补丁 —— git apply 必然拒绝
        # （零上下文、且插入点后面没有尾随上下文，实测 "patch does not apply"）。
        # 它只骗过了不读文件的 validate_patch，于是任务落在 awaiting_patch_approval，
        # 但「应用」永远失败。宁可在这里明确失败，也不要产出一个假装可用的补丁。
        return PatchGenerationResult(
            diff='',
            rationale='fake(valid): 没有可用的 code_context，无法构造可应用的补丁',
            changed_files=[],
            risk_notes=['缺少 code_context，不能构造可应用的补丁'],
            provider='fake', model=None,
            prompt_digest='fake-prompt-digest', response_digest='fake-response-digest',
        )

    def _generate_unapplicable(self, ctx: PatchGenerationContext) -> PatchGenerationResult:
        """Generate a patch with bad line numbers (will fail git apply --check)."""
        target = next((pc.path for pc in ctx.proposed_changes
                       if any(cc.path == pc.path for cc in ctx.code_context)),
                      ctx.proposed_changes[0].path if ctx.proposed_changes else 'src/app.py')
        diff = (
            f"--- a/{target}\n"
            f"+++ b/{target}\n"
            "@@ -9999,3 +9999,4 @@\n"
            " line1\n"
            " line2\n"
            "-line3\n"
            "+line3 modified\n"
        )
        return PatchGenerationResult(
            diff=diff,
            rationale='Patch with mismatched line numbers',
            changed_files=[target],
            risk_notes=['Line numbers do not match context'],
            provider='fake',
            model=None,
            prompt_digest='fake-prompt-digest-unapplicable',
            response_digest='fake-response-digest-unapplicable'
        )

    def _generate_out_of_scope(self, ctx: PatchGenerationContext, plan_json) -> PatchGenerationResult:
        """Generate a patch that modifies files outside plan scope."""
        diff = (
            "--- a/src/other.py\n"
            "+++ b/src/other.py\n"
            "@@ -1,1 +1,2 @@\n"
            " placeholder\n"
            "+# codeforge-fake-patch\n"
        )
        return PatchGenerationResult(
            diff=diff,
            rationale='Patch that modifies files outside the plan',
            changed_files=['src/other.py'],
            risk_notes=['文件不在已确认 Plan 的修改范围内'],
            provider='fake',
            model=None,
            prompt_digest='fake-prompt-digest-out-of-scope',
            response_digest='fake-response-digest-out-of-scope'
        )

    def _generate_dangerous(self, ctx: PatchGenerationContext) -> PatchGenerationResult:
        """Generate a patch targeting a forbidden file."""
        target = next((pc.path for pc in ctx.proposed_changes
                       if any(cc.path == pc.path for cc in ctx.code_context)),
                      ctx.proposed_changes[0].path if ctx.proposed_changes else 'src/app.py')
        diff = (
            f"--- a/{target}\n"
            f"+++ b/{target}\n"
            "@@ -1,1 +1,2 @@\n"
            " placeholder\n"
            "+# codeforge-fake-patch os.remove(target)\n"
        )
        return PatchGenerationResult(
            diff=diff,
            rationale='Patch targeting forbidden file',
            changed_files=[target],
            risk_notes=['Diff 包含潜在危险操作'],
            provider='fake',
            model=None,
            prompt_digest='fake-prompt-digest-dangerous',
            response_digest='fake-response-digest-dangerous'
        )

# ---------------------------------------------------------------------------
# P1-1: Real LLM-backed patch generator (DeepSeek-compatible OpenAI endpoint)
# ---------------------------------------------------------------------------

_DEEPSEEK_DEFAULT_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
_DEEPSEEK_DEFAULT_MODEL = os.environ.get('DEEPSEEK_MODEL', 'deepseek-chat')

_SYSTEM_PROMPT = """You are a precise code-fixing agent.
Given an issue and the relevant source context, produce a minimal unified diff that fixes the issue.
Rules:
- Output ONLY the raw unified diff. No explanation, no markdown code fences.
- Only modify files listed under <proposed_changes>.
- Keep the change minimal and focused on the issue.
- Preserve all unrelated code exactly.
"""


def _digest(text: str) -> str:
    return hashlib.sha256((text or '').encode('utf-8')).hexdigest()[:16]


def _build_user_prompt(ctx: PatchGenerationContext, retry_reason: str | None = None) -> str:
    lines: list[str] = []
    lines.append(f"# Issue\nTitle: {ctx.issue_title}\nBody:\n{ctx.issue_body}\n")
    if ctx.plan_summary:
        lines.append(f"# Plan summary\n{ctx.plan_summary}\n")
    if ctx.reviewer_feedback:
        lines.append(f"# Reviewer feedback\n{ctx.reviewer_feedback}\n")
    lines.append("# Proposed changes (only these files may be modified)")
    for pc in ctx.proposed_changes:
        lines.append(f"- {pc.path}: {pc.description}")
    lines.append("\n# Source context")
    for cc in ctx.code_context:
        lines.append(f"\n## {cc.path} (lines {cc.line_start}-{cc.line_end})")
        lines.append("```")
        lines.append(cc.snippet)
        lines.append("```")
    if retry_reason:
        lines.append(f"\n# Previous attempt failed\n{retry_reason}\nPlease produce a corrected unified diff.")
    return "\n".join(lines)


def _extract_diff(text: str) -> str:
    """Pull a usable unified diff out of an LLM response, or return ''."""
    t = (text or '').strip()
    # 围栏剥离：模型常把「每个文件」各包一个代码块（而不是整份 diff 包一个），
    # 只剥最外层会留下内部的 ``` 行 —— 而 ``` 不是合法 diff 行，git apply 直接报
    # `corrupt patch`（实测 13073）。所以删掉所有「顶格」围栏行。
    # 只认顶格：diff 的上下文/增删行必定带 ' ' / '+' / '-' 前缀，带前缀的 ``` 是
    # 被改文件里的真实内容（如文档字符串中的代码块），不能动。
    t = "\n".join(
        line for line in t.splitlines()
        if not re.fullmatch(r"```[A-Za-z0-9_+.\-]*", line)
    ).strip()
    for marker in ('diff --git', '--- '):
        idx = t.find(marker)
        if idx != -1:
            t = t[idx:]
            break
    if not (t.startswith('--- ') or t.startswith('diff --git')):
        return ''
    # must contain at least one genuine addition/deletion line (not the
    # `--- a/x` / `+++ b/x` headers, which also begin with +/-).
    if not any(re.match(r'^[+-][^-+]', line) for line in t.splitlines()):
        return ''  # no actual change -> not a valid patch
    # A unified diff MUST be newline-terminated. `git apply` rejects a diff whose
    # final line lacks a "\n" with `corrupt patch at <last line>` — and LLMs almost
    # always emit the diff without a trailing newline (the `.strip()` above removes
    # whatever was there). This single line is what makes every LLM patch applicable.
    return t.rstrip('\n') + '\n'


def _changed_files(diff: str) -> list[str]:
    files: list[str] = []
    for line in diff.splitlines():
        if line.startswith('+++ b/'):
            files.append(line[6:].strip())
        elif line.startswith('diff --git '):
            parts = line.split()
            if len(parts) >= 4:
                files.append(parts[-1][2:])  # strip leading 'b/'
    # de-dup, keep order
    seen: set[str] = set()
    out: list[str] = []
    for f in files:
        if f and f not in seen:
            seen.add(f)
            out.append(f)
    return out


class LLMPatchGenerator:
    """Calls a DeepSeek-compatible OpenAI endpoint to generate a real patch.

    On any failure (network error, empty/garbage response) it retries up to
    `max_retries` times with the failure reason appended to the prompt. If all
    attempts fail, it falls back to `FakePatchGenerator` so the closed loop in
    the demo never breaks.
    """

    def __init__(self, api_key: str, base_url: str = _DEEPSEEK_DEFAULT_BASE_URL,
                 model: str = _DEEPSEEK_DEFAULT_MODEL, max_retries: int = 2,
                 timeout: float = 60.0, fallback: PatchGenerator | None = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout
        self.fallback = fallback or FakePatchGenerator('valid')

    async def generate(self, ctx: PatchGenerationContext) -> PatchGenerationResult:
        if not self.api_key:
            res = await self.fallback.generate(ctx)
            res.risk_notes.append('LLM 未配置 API key，已回退到 Fake 补丁')
            res.provider = 'fake(fallback)'
            return res

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(ctx)},
        ]
        last_reason = None
        for attempt in range(self.max_retries + 1):
            try:
                raw = await self._call(messages)
            except Exception as exc:  # network/timeout/HTTP error
                last_reason = f"LLM 调用异常: {exc}"
                messages[-1] = {"role": "user", "content": _build_user_prompt(ctx, retry_reason=last_reason)}
                continue
            diff = _extract_diff(raw)
            if diff:
                return PatchGenerationResult(
                    diff=diff,
                    rationale=f'LLM 生成补丁（{self.model}，第 {attempt + 1} 次尝试）',
                    changed_files=_changed_files(diff),
                    risk_notes=[],
                    provider='deepseek',
                    model=self.model,
                    prompt_digest=_digest(messages[-1]['content']),
                    response_digest=_digest(raw),
                )
            last_reason = 'LLM 返回内容无法解析为有效的 unified diff'
            messages[-1] = {"role": "user", "content": _build_user_prompt(ctx, retry_reason=last_reason)}

        res = await self.fallback.generate(ctx)
        res.risk_notes.append(f"LLM 生成失败（{last_reason}），已回退到 Fake 补丁")
        res.provider = 'fake(fallback)'
        return res

    async def _call(self, messages: list[dict]) -> str:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages, "temperature": 0.0, "stream": False}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]


# Module-level generator for injection (tests use monkeypatch.setattr).
# Default is Fake unless a DeepSeek key is present and not explicitly disabled.
_FORCE = os.environ.get('CODEFORGE_GENERATOR', 'auto').lower()
if _FORCE == 'fake' or (_FORCE == 'auto' and not os.environ.get('DEEPSEEK_API_KEY')):
    _generator: PatchGenerator = FakePatchGenerator('valid')
    print('[patch_generator] 使用 FakePatchGenerator（未检测到 DEEPSEEK_API_KEY 或 CODEFORGE_GENERATOR=fake）')
else:
    _generator = LLMPatchGenerator(api_key=os.environ['DEEPSEEK_API_KEY'])
    print(f'[patch_generator] 使用 LLMPatchGenerator: model={_generator.model}, base_url={_generator.base_url}')


async def generate_patch(ctx: PatchGenerationContext) -> PatchGenerationResult:
    """Generate patch using injected generator."""
    return await _generator.generate(ctx)
