"""Phase 3 agent contracts and deterministic graph nodes.

The state shape is intentionally compatible with a future LangGraph runner.
The first node is local and deterministic so its output can be tested and
audited before an LLM provider is introduced.
"""
from __future__ import annotations

import re
from typing import Literal, TypedDict

from pydantic import BaseModel, Field

from code_search import search_repository
from rag_adapter import search_with_fallback


class RequirementAnalysis(BaseModel):
    task_type: Literal["bug_fix", "function_optimization", "validation", "unknown"]
    objective: str = Field(min_length=1, max_length=500)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=10)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    risk_level: Literal["low", "medium", "high"]
    risk_notes: list[str] = Field(default_factory=list, max_length=10)
    missing_context: list[str] = Field(default_factory=list, max_length=10)


class CodeContext(BaseModel):
    path: str
    line_start: int
    line_end: int
    matched_line: int
    symbol: str | None = None
    snippet: str
    match_type: str


class ProposedChange(BaseModel):
    path: str
    symbol: str | None = None
    reason: str


class ChangePlan(BaseModel):
    summary: str = Field(min_length=1, max_length=1000)
    proposed_changes: list[ProposedChange] = Field(default_factory=list, max_length=5)
    impact_scope: Literal["single_file", "multiple_files", "unknown"]
    test_plan: list[str] = Field(default_factory=list, max_length=5)
    risk_level: Literal["low", "medium", "high"]
    missing_context: list[str] = Field(default_factory=list, max_length=10)
    reviewer_feedback: str | None = None


class PlanReview(BaseModel):
    approved: bool
    risk_level: Literal["low", "medium", "high"]
    findings: list[str] = Field(default_factory=list, max_length=10)
    review_focus: list[str] = Field(default_factory=list, max_length=10)


class AgentState(TypedDict, total=False):
    task_id: int
    repository_id: int
    issue_title: str
    issue_body: str
    requirement: RequirementAnalysis
    code_context: list[CodeContext]
    change_plan: ChangePlan
    reviewer_feedback: str | None
    plan_review: PlanReview
    errors: list[str]
    search_trace: list[dict]
    template_params: dict


_STOP_WORDS = {"the", "and", "for", "with", "this", "that", "please", "修复", "增加", "优化", "一个", "功能", "问题", "代码", "任务"}
_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}|[\u4e00-\u9fff]{2,}")


def _task_type(text: str) -> str:
    lowered = text.casefold()
    if any(word in lowered for word in ("校验", "验证", "validation", "validate", "guard")):
        return "validation"
    if any(word in lowered for word in ("优化", "性能", "refactor", "optimize")):
        return "function_optimization"
    if any(word in lowered for word in ("修复", "错误", "异常", "bug", "fix", "fail")):
        return "bug_fix"
    return "unknown"


def analyze_requirement(issue_title: str, issue_body: str, params: dict | None = None) -> RequirementAnalysis:
    """Turn an Issue into an auditable, conservative search brief.

    params 是任务模板快照（TemplateParams.model_dump()）；None / 空 dict 时与无模板逐字节一致。
    task_type 强制分类；keywords / acceptance_criteria 模板项在前、优先存活截断；
    risk_keywords 并入风险触发词表 —— 命中才记风险条目，并留一条可辨认的"命中模板风险词"痕迹。
    """
    text = f"{issue_title}\n{issue_body}".strip()
    p = params or {}
    kind = p.get("task_type") or _task_type(text)
    template_keywords = [word for word in p.get("keywords", []) if word]
    keywords: list[str] = list(template_keywords)
    seen_keywords = {word.casefold() for word in template_keywords}
    for token in _TOKEN_RE.findall(text):
        normalized = token.casefold()
        if normalized not in _STOP_WORDS and normalized not in seen_keywords:
            keywords.append(normalized)
            seen_keywords.add(normalized)
    template_criteria = [item for item in p.get("acceptance_criteria", []) if item]
    extracted_criteria = [part.strip(" -:：") for part in re.split(r"[\n。；;]+", issue_body) if part.strip(" -:：")]
    criteria = template_criteria + [item for item in extracted_criteria if item not in template_criteria]
    risks: list[str] = []
    lowered = text.casefold()
    hit_template_risks = [word for word in p.get("risk_keywords", []) if word and word.casefold() in lowered]
    if any(word in lowered for word in ("auth", "权限", "token", "secret", "密码", "支付", "删除", "delete")) or hit_template_risks:
        risks.append("任务涉及安全、权限或破坏性操作，需要人工重点审查")
    if hit_template_risks:
        risks.append("命中模板风险词: " + "、".join(hit_template_risks))
    if any(word in lowered for word in ("数据库", "database", "迁移", "migration")):
        risks.append("任务可能影响持久化数据或迁移兼容性")
    missing: list[str] = []
    risk_level = "high" if risks else "low"
    return RequirementAnalysis(
        task_type=kind,
        objective=issue_title.strip() or issue_body.strip(),
        acceptance_criteria=criteria[: p.get("max_acceptance_criteria", 10)],
        keywords=keywords[: p.get("max_keywords", 12)],
        risk_level=risk_level,
        risk_notes=risks,
        missing_context=missing,
    )


def run_requirement_node(state: AgentState) -> AgentState:
    state["requirement"] = analyze_requirement(state["issue_title"], state["issue_body"], state.get("template_params"))
    return state


def run_code_search_node(state: AgentState, repository_root: str, allowed_source_roots: list[str]) -> AgentState:
    """Search a small keyword budget and retain unique, auditable locations."""
    requirement = state["requirement"]
    params = state.get("template_params") or {}
    keyword_budget = params.get("search_keyword_budget", 8)
    per_keyword_max_results = params.get("per_keyword_max_results", 4)
    result_limit = params.get("search_result_limit", 8)
    contexts: list[CodeContext] = []
    seen: set[tuple[str, int]] = set()
    trace: list[dict] = []
    # 模板锚点优先于关键词检索：显式指定的文件 / 符号位置先入结果，剩余预算再留给关键词。
    anchor_paths = [item for item in params.get("anchor_paths", []) if item]
    anchor_symbols = [item for item in params.get("anchor_symbols", []) if item]
    if anchor_paths or anchor_symbols:
        for anchor in anchor_paths:
            normalized = anchor.replace("\\", "/").strip("/")
            for item in search_repository(repository_root, "", filename=normalized.split("/")[-1], allowed_source_roots=allowed_source_roots, max_results=per_keyword_max_results):
                # filename 是子串匹配，锚点要的是确切文件：按完整相对路径收口
                candidate = item["path"].replace("\\", "/")
                if candidate != normalized and not candidate.endswith("/" + normalized):
                    continue
                key = (item["path"], item["matched_line"])
                if key in seen:
                    continue
                seen.add(key)
                contexts.append(CodeContext.model_validate(item))
                if len(contexts) >= result_limit:
                    break
            if len(contexts) >= result_limit:
                break
        for symbol in anchor_symbols:
            for item in search_repository(repository_root, "", symbol=symbol, allowed_source_roots=allowed_source_roots, max_results=per_keyword_max_results):
                key = (item["path"], item["matched_line"])
                if key in seen:
                    continue
                seen.add(key)
                contexts.append(CodeContext.model_validate(item))
                if len(contexts) >= result_limit:
                    break
            if len(contexts) >= result_limit:
                break
        trace.append({"strategy": "template_anchors", "hits": len(contexts), "anchor_paths": anchor_paths, "anchor_symbols": anchor_symbols})
    # Chinese issue language is expanded into common English identifiers used by source code.
    aliases = {"输入":"input", "长度":"length", "校验":"validation validate", "验证":"validation validate", "表单":"form", "配置":"config configuration", "错误":"error", "保存":"save"}
    aliases.update(params.get("search_aliases") or {})
    candidates = []
    path_tokens = re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", f"{state.get('issue_title','')} {state.get('issue_body','')}")
    for keyword in requirement.keywords[:keyword_budget]:
        translated = aliases.get(keyword)
        if translated is None:
            translated = " ".join(value for key, value in aliases.items() if key in keyword)
        candidates.extend((translated or keyword).split())
    # Append feedback to candidates and path_tokens if present
    feedback = state.get("reviewer_feedback", "")
    if feedback:
        feedback_candidates = _TOKEN_RE.findall(feedback)
        candidates.extend(token.casefold() for token in feedback_candidates if token.casefold() not in candidates)
        feedback_paths = re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", feedback)
        path_tokens.extend(token for token in feedback_paths if token not in path_tokens)
    for keyword in dict.fromkeys(candidates):
        if len(contexts) >= result_limit:
            break
        for item in search_repository(repository_root, keyword, allowed_source_roots=allowed_source_roots, max_results=per_keyword_max_results):
            key = (item["path"], item["matched_line"])
            if key in seen:
                continue
            seen.add(key)
            contexts.append(CodeContext.model_validate(item))
            if len(contexts) >= result_limit:
                state["code_context"] = contexts
                trace.append({"strategy":"english_candidates","hits":len(contexts)})
                state["search_trace"] = trace
                return state
    trace.append({"strategy":"english_candidates","hits":len(contexts)})
    if not contexts and path_tokens:
        for token in path_tokens:
            for item in search_repository(repository_root, "", filename=token.split('/')[-1], allowed_source_roots=allowed_source_roots, max_results=8):
                key=(item['path'],item['matched_line'])
                if key not in seen:
                    seen.add(key); contexts.append(CodeContext.model_validate(item))
        trace.append({"strategy":"path_tokens","hits":len(contexts),"tokens":path_tokens})
    if not contexts:
        rag = search_with_fallback(repository_root, " ".join(candidates), allowed_source_roots=allowed_source_roots, max_results=8)
        rag_results = list(rag)
        contexts = [CodeContext.model_validate(x) for x in rag_results]
        trace.append({"strategy":"rag","status":rag.status,"hits":len(contexts)})
    if not contexts: trace.append({"strategy":"blocked","hits":0})
    state["code_context"] = contexts
    state["search_trace"] = trace
    return state


def run_plan_node(state: AgentState) -> AgentState:
    """Create a conservative plan from retrieved code, never a code change."""
    requirement = state["requirement"]
    contexts = state.get("code_context", [])
    params = state.get("template_params") or {}
    missing = list(requirement.missing_context)
    if not contexts:
        missing.append("未检索到相关源码位置，不能安全地产生修改方案")
        state["change_plan"] = ChangePlan(
            summary="未找到足够的代码上下文，等待人工补充 Issue 或调整检索范围。",
            impact_scope="unknown",
            risk_level="high" if requirement.risk_level == "high" else "medium",
            missing_context=missing,
            reviewer_feedback=state.get("reviewer_feedback"),
        )
        return state
    changes: list[ProposedChange] = []
    seen_paths: set[str] = set()
    max_proposed_changes = params.get("max_proposed_changes", 5)
    for context in contexts:
        if context.path in seen_paths:
            continue
        seen_paths.add(context.path)
        location = f"{context.path}:{context.matched_line}"
        changes.append(ProposedChange(path=context.path, symbol=context.symbol, reason=f"{location} 与需求关键词匹配，需人工确认后分析最小修改范围"))
        if len(changes) >= max_proposed_changes:
            break
    impact = "single_file" if len(changes) == 1 else "multiple_files"
    # 模板的 test_plan 只写入方案与事件供人工审查参考，不决定链路跑哪条测试
    template_test_plan = [item for item in params.get("test_plan", []) if item]
    state["change_plan"] = ChangePlan(
        summary=f"针对“{requirement.objective}”建议先审查 {len(changes)} 个命中源码位置，确认后再生成 Patch。",
        proposed_changes=changes,
        impact_scope=impact,
        test_plan=template_test_plan or ["为目标行为补充或更新回归测试", "在人工确认后执行仓库白名单测试"],
        risk_level=requirement.risk_level,
        missing_context=missing,
        reviewer_feedback=state.get("reviewer_feedback"),
    )
    return state


def run_plan_review_node(state: AgentState) -> AgentState:
    """Review scope and context sufficiency before a human confirmation point."""
    plan = state["change_plan"]
    findings: list[str] = []
    if plan.impact_scope == "unknown":
        findings.append("修改范围未知")
    if not plan.test_plan:
        findings.append("缺少测试计划")
    if plan.risk_level == "high":
        findings.append("涉及安全/权限/破坏性操作，人工确认需说明放行理由")
    state["plan_review"] = PlanReview(
        approved=not plan.missing_context,
        risk_level=plan.risk_level,
        findings=findings[:10],
        review_focus=[],
    )
    return state
