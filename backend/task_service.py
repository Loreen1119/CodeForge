"""Task lifecycle, event persistence and the Phase 1 no-op runner."""
from __future__ import annotations
import asyncio, json, os
from datetime import datetime, timezone
from typing import Any
import aiosqlite
from db import DB_PATH
from agent_graph import AgentState, run_code_search_node, run_plan_node, run_plan_review_node, run_requirement_node
from repository_service import inspect_repository
from patch_service import DEFAULT_MAX_CHANGED_LINES, validate_patch
from patch_generator import generate_patch, FakePatchGenerator, LLMPatchGenerator
from template_service import parse_params
from constants import AWAITING_PLAN_APPROVAL, NEEDS_CONTEXT, RUNNING, PENDING, FAILED, GENERATING_PATCH, AWAITING_PATCH_GENERATION_RETRY, AWAITING_PATCH_APPROVAL

active_tasks: dict[int, asyncio.Task] = {}
def _now(): return datetime.now(timezone.utc).isoformat()
def load_template_params(row) -> dict:
    """把 code_tasks 行上的模板参数快照读成完整 dict；无模板（NULL）给空 dict。

    解析统一走 template_service.parse_params：同一库列只留这一套逻辑，
    参数表演进时对旧快照的容忍（未知键忽略）在这里集中处理。
    """
    raw = row['template_params_json']
    return parse_params(raw).model_dump() if raw else {}
async def _conn():
    c=await aiosqlite.connect(str(DB_PATH)); c.row_factory=aiosqlite.Row; return c
async def create_repository(data: dict[str, Any]) -> int:
    c=await _conn()
    try:
        cur=await c.execute("INSERT INTO repositories(name,local_path,default_branch,allowed_source_roots,allowed_test_commands) VALUES(?,?,?,?,?)", (data['name'],data['local_path'],data.get('default_branch','main'),json.dumps(data.get('allowed_source_roots',[])),json.dumps(data.get('allowed_test_commands',[])))); await c.commit(); return cur.lastrowid
    finally: await c.close()
async def list_repositories():
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM repositories ORDER BY id DESC")).fetchall()
    finally: await c.close()
async def get_repository(repository_id):
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM repositories WHERE id=?",(repository_id,))).fetchone()
    finally: await c.close()
async def create_task_record(data: dict[str, Any]) -> int:
    c=await _conn()
    try:
        cur=await c.execute("INSERT INTO code_tasks(repository_id,issue_title,issue_body,status,template_id,template_params_json) VALUES(?,?,?,'pending',?,?)",(data['repository_id'],data['issue_title'],data['issue_body'],data.get('template_id'),data.get('template_params_json'))); await c.commit(); return cur.lastrowid
    finally: await c.close()
async def get_task(task_id):
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM code_tasks WHERE id=?",(task_id,))).fetchone()
    finally: await c.close()
async def list_tasks():
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM code_tasks ORDER BY id DESC")).fetchall()
    finally: await c.close()
async def update_task(task_id, status, **fields):
    sets=['status=?']; vals=[status]
    for k,v in fields.items(): sets.append(f'{k}=?'); vals.append(v)
    vals.append(task_id); c=await _conn()
    try: await c.execute(f"UPDATE code_tasks SET {','.join(sets)} WHERE id=?",vals); await c.commit()
    finally: await c.close()
async def request_reanalysis(task_id:int, feedback:str) -> bool:
    c=await _conn()
    try:
        await c.execute("BEGIN IMMEDIATE")
        cur=await c.execute("UPDATE code_tasks SET status='pending', current_agent=NULL, plan_decision=NULL, reviewer_feedback=?, attempt_number=attempt_number+1, error_message=NULL, finished_at=NULL WHERE id=? AND status IN (?,?)",(feedback,task_id,AWAITING_PLAN_APPROVAL,NEEDS_CONTEXT))
        if cur.rowcount != 1:
            await c.rollback(); return False
        await c.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status IN ('reviewed','approved','rejected')",(task_id,))
        await c.execute("UPDATE patches SET decision_status='superseded' WHERE task_id=? AND decision_status='pending'",(task_id,))
        await c.commit(); return True
    finally: await c.close()
async def create_patch(task_id:int, attempt_number:int, diff:str, check:dict) -> int:
    c=await _conn()
    try:
        # Get the next patch_revision for this plan
        plan_id = check.get('plan_id')
        if plan_id:
            row = await (await c.execute(
                "SELECT MAX(patch_revision) as max_rev FROM patches WHERE plan_id=?",
                (plan_id,)
            )).fetchone()
            patch_revision = (row['max_rev'] or 0) + 1
        else:
            patch_revision = 1

        cur=await c.execute("INSERT INTO patches(task_id,patch_revision,base_commit,plan_id,diff,changed_files,changed_lines,risk_level,security_findings,validation_status,decision_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",(task_id,patch_revision,check.get('base_commit'),plan_id,diff,json.dumps(check['changed_files']),check['changed_lines'],check['risk_level'],json.dumps(check['findings'],ensure_ascii=False),'valid' if check.get('valid') else 'invalid','pending' if check.get('valid') else 'invalid'))
        await c.commit(); return cur.lastrowid
    finally: await c.close()
async def get_patch(patch_id:int):
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM patches WHERE id=?",(patch_id,))).fetchone()
    finally: await c.close()
async def create_test_run(task_id:int, patch_id:int, result:dict, command:str) -> int:
    c=await _conn()
    try:
        cur=await c.execute("INSERT INTO test_runs(task_id,patch_id,command,status,exit_code,output,duration_ms,timed_out,environment_error) VALUES(?,?,?,?,?,?,?,?,?)",(task_id,patch_id,command,result.get('status'),result.get('exit_code'),result.get('output',''),result.get('duration_ms'),1 if result.get('timed_out') else 0,result.get('environment_error')))
        await c.commit(); return cur.lastrowid
    finally: await c.close()
async def list_test_runs(task_id:int):
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM test_runs WHERE task_id=? ORDER BY id",(task_id,))).fetchall()
    finally: await c.close()
async def create_plan(task_id:int, attempt_number:int, plan:dict, review:dict, base_commit:str|None) -> int:
    c=await _conn()
    try:
        auto_review_passed = 1 if (review.get('approved') and not review.get('findings')) else 0
        cur=await c.execute("INSERT INTO plans(task_id,attempt_number,status,plan_json,review_json,auto_review_passed,base_commit) VALUES(?,?,?,?,?,?,?)",(task_id,attempt_number,'reviewed',json.dumps(plan,ensure_ascii=False),json.dumps(review,ensure_ascii=False),auto_review_passed,base_commit))
        await c.commit(); return cur.lastrowid
    finally: await c.close()
async def get_plan(plan_id:int):
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM plans WHERE id=?",(plan_id,))).fetchone()
    finally: await c.close()
async def get_current_plan(task_id:int):
    c=await _conn()
    try: return await (await c.execute("SELECT * FROM plans WHERE task_id=? AND status IN ('reviewed','approved') ORDER BY id DESC LIMIT 1",(task_id,))).fetchone()
    finally: await c.close()
async def decide_plan(plan_id:int, status:str, override_reason:str|None) -> bool:
    c=await _conn()
    try:
        cur=await c.execute("UPDATE plans SET status=?, override_reason=?, reviewer_id='local_user', reviewed_at=? WHERE id=? AND status='reviewed'",(status,override_reason,_now(),plan_id))
        await c.commit(); return cur.rowcount == 1
    finally: await c.close()
async def supersede_for_reanalysis(task_id:int) -> None:
    c=await _conn()
    try:
        await c.execute("BEGIN IMMEDIATE")
        await c.execute("UPDATE plans SET status='superseded' WHERE task_id=? AND status IN ('reviewed','approved','rejected')",(task_id,))
        await c.execute("UPDATE patches SET decision_status='superseded' WHERE task_id=? AND decision_status='pending'",(task_id,))
        await c.commit()
    finally: await c.close()
async def update_patch(patch_id:int, **fields):
    if not fields: return
    sets=[f"{key}=?" for key in fields]
    values=list(fields.values())+[patch_id]
    c=await _conn()
    try: await c.execute(f"UPDATE patches SET {','.join(sets)} WHERE id=?",values); await c.commit()
    finally: await c.close()
async def append_event(task_id:int,event_type:str,payload:dict[str,Any],message:str|None=None,agent_name:str|None=None)->int:
    c=await _conn()
    try:
        await c.execute('BEGIN IMMEDIATE'); row=await (await c.execute('SELECT next_event_seq FROM code_tasks WHERE id=?',(task_id,))).fetchone()
        if row is None: raise ValueError('task not found')
        seq=int(row[0]); await c.execute('INSERT INTO task_events(task_id,event_seq,event_type,agent_name,message,payload_json) VALUES(?,?,?,?,?,?)',(task_id,seq,event_type,agent_name,message,json.dumps(payload,ensure_ascii=False))); await c.execute('UPDATE code_tasks SET next_event_seq=? WHERE id=?',(seq+1,task_id)); await c.commit(); return seq
    finally: await c.close()
async def list_events(task_id, after_seq=0):
    c=await _conn()
    try:
        rows=await (await c.execute('SELECT event_seq,event_type,agent_name,message,payload_json,created_at FROM task_events WHERE task_id=? AND event_seq>? ORDER BY event_seq',(task_id,after_seq))).fetchall()
        return [(int(r[0]),r[1],r[2],r[3],json.loads(r[4]),r[5]) for r in rows]
    finally: await c.close()
async def run_task(task_id):
    try:
        row=await get_task(task_id)
        if row is None: return
        await update_task(task_id,RUNNING,started_at=_now(),current_agent='requirement_agent')
        await append_event(task_id,'TASK_STARTED',{'type':'TASK_STARTED','status':RUNNING},'代码任务已启动')
        template_params=load_template_params(row)
        state: AgentState = {'task_id':task_id,'repository_id':row['repository_id'],'issue_title':row['issue_title'],'issue_body':row['issue_body'],'reviewer_feedback':row['reviewer_feedback'],'template_params':template_params}
        state=run_requirement_node(state)
        analysis=state['requirement']
        requirement_payload={'type':'REQUIREMENT_ANALYZED','analysis':analysis.model_dump()}
        if row['template_id'] is not None: requirement_payload['template_id']=row['template_id']
        await append_event(task_id,'REQUIREMENT_ANALYZED',requirement_payload,'需求分析完成','requirement_agent')
        repository=await get_repository(row['repository_id'])
        if repository is None: raise ValueError('repository not found')
        roots=json.loads(repository['allowed_source_roots'])
        info=inspect_repository(repository['local_path'], roots)
        await update_task(task_id,RUNNING,current_agent='code_search_agent')
        state=run_code_search_node(state,info['git_root'],roots)
        strategies=list(state.get('search_trace',[]))
        if template_params: strategies.append({'strategy':'template_applied','params':template_params})
        await append_event(task_id,'CODE_SEARCHED',{'type':'CODE_SEARCHED','results':[item.model_dump() for item in state['code_context']], 'strategies': strategies},f"代码检索完成，命中 {len(state['code_context'])} 个位置",'code_search_agent')
        await update_task(task_id,RUNNING,current_agent='plan_agent')
        state=run_plan_node(state)
        state=run_plan_review_node(state)
        plan_id=await create_plan(task_id,row['attempt_number'],state['change_plan'].model_dump(),state['plan_review'].model_dump(),info.get('base_commit'))
        await update_task(task_id,RUNNING,current_agent='plan_agent')
        await append_event(task_id,'PLAN_DRAFTED',{'type':'PLAN_DRAFTED','plan_id':plan_id,'plan':state['change_plan'].model_dump()},'修改方案草案已生成','plan_agent')
        await append_event(task_id,'PLAN_REVIEWED',{'type':'PLAN_REVIEWED','plan_id':plan_id,'review':state['plan_review'].model_dump()},'方案自动审查完成','plan_review_agent')
        plan_ok=bool(state['plan_review'].approved and state['change_plan'].proposed_changes)
        if plan_ok:
            await append_event(task_id,'TASK_READY',{'type':'TASK_READY','status':AWAITING_PLAN_APPROVAL},'方案已通过自动审查，等待人工确认','plan_review_agent')
            await update_task(task_id,AWAITING_PLAN_APPROVAL,finished_at=None,current_agent=None)
        else:
            await append_event(task_id,'NEEDS_CONTEXT',{'type':'NEEDS_CONTEXT','status':NEEDS_CONTEXT,'findings':state['plan_review'].findings,'missing_context':state['change_plan'].missing_context},'方案未通过审查，需要补充上下文','plan_review_agent')
            await update_task(task_id,NEEDS_CONTEXT,finished_at=None,current_agent=None)
    except asyncio.CancelledError: raise
    except Exception as exc:
        message=str(exc)[:1000] or exc.__class__.__name__
        await update_task(task_id,FAILED,finished_at=_now(),current_agent=None,error_message=message)
        await append_event(task_id,'TASK_FAILED',{'type':'TASK_FAILED','status':FAILED,'error':message},'任务执行失败，未修改目标仓库')
    finally: active_tasks.pop(task_id,None)
async def schedule_task(task_id):
    if task_id in active_tasks: return False
    task=asyncio.create_task(run_task(task_id)); active_tasks[task_id]=task; return True
async def shutdown_unfinished_tasks():
    for task in list(active_tasks.values()):
        if not task.done(): task.cancel()
    if active_tasks: await asyncio.gather(*active_tasks.values(),return_exceptions=True)
    active_tasks.clear()

async def transition_task(task_id: int, to_status: str, event_type: str, *,
                         from_statuses: list[str] | None = None, payload: dict[str, Any] | None = None,
                         message: str | None = None, agent_name: str | None = None,
                         **fields) -> bool:
    """Atomically transition task status and record event in single transaction.

    Args:
        task_id: Task ID
        to_status: Target status
        event_type: Event type to record
        from_statuses: If provided, transition only if current status is in this list
        payload: Event payload (defaults to {'type': event_type})
        message: Event message
        agent_name: Agent name for audit trail
        **fields: Additional fields to update on task (e.g., finished_at, error_message)

    Returns:
        True if transition succeeded, False if current status doesn't match or task not found
    """
    if payload is None:
        payload = {'type': event_type}

    c = await _conn()
    try:
        await c.execute('BEGIN IMMEDIATE')

        # Check current status and get event_seq
        row = await (await c.execute(
            'SELECT status, next_event_seq FROM code_tasks WHERE id=?', (task_id,)
        )).fetchone()

        if row is None:
            await c.rollback()
            return False

        current_status = row['status']
        next_seq = int(row['next_event_seq'])

        # If from_statuses specified, verify current status is in the list
        if from_statuses and current_status not in from_statuses:
            await c.rollback()
            return False

        # Build UPDATE statement for status and other fields
        update_fields = ['status = ?']
        update_values = [to_status]

        for key, value in fields.items():
            update_fields.append(f'{key} = ?')
            update_values.append(value)

        update_values.append(task_id)

        # Update task status (and other fields)
        cur = await c.execute(
            f"UPDATE code_tasks SET {', '.join(update_fields)} WHERE id = ?",
            update_values
        )

        if cur.rowcount != 1:
            await c.rollback()
            return False

        # Insert event
        await c.execute(
            'INSERT INTO task_events(task_id, event_seq, event_type, agent_name, message, payload_json) VALUES(?, ?, ?, ?, ?, ?)',
            (task_id, next_seq, event_type, agent_name, message, json.dumps(payload, ensure_ascii=False))
        )

        # Update next_event_seq
        await c.execute(
            'UPDATE code_tasks SET next_event_seq = ? WHERE id = ?',
            (next_seq + 1, task_id)
        )

        await c.commit()
        return True

    finally:
        await c.close()

async def transition_patch(patch_id: int, apply_status: str, *,
                          apply_failure_reason: str | None = None,
                          **fields) -> bool:
    """Atomically update patch apply status and record event in single transaction.

    Args:
        patch_id: Patch ID
        apply_status: Target apply_status value
        apply_failure_reason: Failure reason (if any)
        **fields: Additional fields to update on patch

    Returns:
        True if update succeeded, False if patch not found
    """
    c = await _conn()
    try:
        await c.execute('BEGIN IMMEDIATE')

        # Get patch for event recording
        row = await (await c.execute('SELECT * FROM patches WHERE id = ?', (patch_id,))).fetchone()
        if row is None:
            await c.rollback()
            return False

        task_id = row['task_id']

        # Update patch
        update_fields = ['apply_status = ?']
        update_values = [apply_status]

        if apply_failure_reason is not None:
            update_fields.append('apply_failure_reason = ?')
            update_values.append(apply_failure_reason)

        for key, value in fields.items():
            update_fields.append(f'{key} = ?')
            update_values.append(value)

        update_values.append(patch_id)

        cur = await c.execute(
            f"UPDATE patches SET {', '.join(update_fields)} WHERE id = ?",
            update_values
        )

        if cur.rowcount != 1:
            await c.rollback()
            return False

        # Get next event sequence for the task
        task_row = await (await c.execute(
            'SELECT next_event_seq FROM code_tasks WHERE id = ?', (task_id,)
        )).fetchone()

        next_seq = int(task_row['next_event_seq'])

        # Record PATCH_APPLY_FAILED event if failure reason provided
        if apply_failure_reason is not None:
            payload = {
                'type': 'PATCH_APPLY_FAILED',
                'patch_id': patch_id,
                'apply_failure_reason': apply_failure_reason
            }
            await c.execute(
                'INSERT INTO task_events(task_id, event_seq, event_type, agent_name, message, payload_json) VALUES(?, ?, ?, ?, ?, ?)',
                (task_id, next_seq, 'PATCH_APPLY_FAILED', 'patch_agent',
                 f'Patch 应用失败: {apply_failure_reason}', json.dumps(payload, ensure_ascii=False))
            )
            await c.execute(
                'UPDATE code_tasks SET next_event_seq = ? WHERE id = ?',
                (next_seq + 1, task_id)
            )

        await c.commit()
        return True

    finally:
        await c.close()

# 评测档位：Plan 范围一致性默认开启（产品原行为），显式设 0 才关闭。
# 关闭的动机只可能是评测——真实 issue 的修复文件常常不落在「由 issue 正文推出的方案范围」内。
ENFORCE_PLAN_SCOPE = os.environ.get("CODEFORGE_ENFORCE_PLAN_SCOPE", "1").strip().lower() not in ("0", "false", "no")

# 评测档位：变更行数上限。产品路径不读它 —— 未显式设 env 时唯一来源仍是模板快照
# （模板「只能收窄、硬上限 50」的不变量由 TemplateParams 契约把守，这里不越权）。
# 只有真实 issue 评测会显式设大（如 400），因为上游 PR 的改动规模普遍超过 50 行；
# 若不显式覆盖，模板里恒存在的 max_changed_lines 会把 env 值吃掉，变成"设了也没生效"。
ENV_MAX_CHANGED_LINES = (
    int(os.environ["CODEFORGE_MAX_CHANGED_LINES"])
    if os.environ.get("CODEFORGE_MAX_CHANGED_LINES", "").strip()
    else None
)


def effective_max_changed_lines(params: dict) -> int:
    """评测档优先；未显式设 env 时保持「模板只能收窄」的原语义。"""
    if ENV_MAX_CHANGED_LINES is not None:
        return ENV_MAX_CHANGED_LINES
    return params.get("max_changed_lines", DEFAULT_MAX_CHANGED_LINES)


async def validate_and_create_patch(task_id: int, attempt_number: int, diff: str, plan_id: int, base_commit: str | None, plan_json: dict, git_root: str, allowed_roots: list[str], max_changed_lines: int = DEFAULT_MAX_CHANGED_LINES) -> tuple:
    """Validate patch, check scope, and create patch record. Returns (patch_id, check, valid).

    base_commit 必须由调用方从 plans 表的列传入（plan['base_commit']）——
    plan_json 里没有这个键，从它取会恒为 None，静默削弱基准提交守卫。

    max_changed_lines 由调用方从任务模板快照透传（模板只能收窄，
    50 行硬上限已在 TemplateParams 契约层卡死，这里不做任何放宽）。
    """
    check = validate_patch(diff, git_root, allowed_roots, max_changed_lines=max_changed_lines)
    if ENFORCE_PLAN_SCOPE and check.valid and not set(check.changed_files).issubset({item['path'] for item in plan_json['proposed_changes']}):
        check.valid = False
        check.findings.append('Patch 文件不属于已确认 Plan 的修改范围')
        check.risk_level = 'high'
    check_data = check.model_dump()
    check_data.update({'plan_id': plan_id, 'base_commit': base_commit})
    patch_id = await create_patch(task_id, attempt_number, diff, check_data)
    return patch_id, check, check.valid

async def run_generate_patch(task_id: int) -> None:
    """Background task: generate patch for an approved plan."""
    from schemas import PatchGenerationContext, CodeContext, ProposedChange
    from repository_service import inspect_repository

    try:
        task_row = await get_task(task_id)
        if task_row is None:
            return
        params = load_template_params(task_row)

        plan = await get_current_plan(task_id)
        if plan is None or plan['status'] != 'approved':
            return

        repo = await get_repository(task_row['repository_id'])
        if repo is None:
            return

        roots = json.loads(repo['allowed_source_roots'])
        info = inspect_repository(repo['local_path'], roots)

        # Build generation context
        plan_json = json.loads(plan['plan_json'])
        task_dict = dict(task_row)
        plan_dict = dict(plan)
        proposed_changes = [
            ProposedChange(path=pc['path'], description=pc.get('description', ''))
            for pc in plan_json.get('proposed_changes', [])
        ]
        # code_context 不在 plan_json 里（ChangePlan 的字段是
        # summary/proposed_changes/impact_scope/test_plan/risk_level/missing_context/reviewer_feedback），
        # 所以原来那句 `plan_json.get('code_context', [])` 恒为空，
        # 导致 FakePatchGenerator._generate_valid 的主分支成了死代码、每次都落到 fallback。
        # 真实来源是该任务最近一条 CODE_SEARCHED 事件（run_task 里就是这么写进去的）。
        code_context = []
        for _seq, _etype, _agent, _msg, _payload, _ts in await list_events(task_id):
            if _etype == 'CODE_SEARCHED':
                code_context = [
                    CodeContext.model_validate(item) for item in _payload.get('results', [])
                ]

        ctx = PatchGenerationContext(
            task_id=task_id,
            repository_id=task_row['repository_id'],
            plan_id=plan['id'],
            issue_title=task_dict.get('issue_title', ''),
            issue_body=task_dict.get('issue_body', ''),
            plan_summary=plan_json.get('summary', ''),
            proposed_changes=proposed_changes,
            code_context=code_context,
            base_commit=plan_dict.get('base_commit'),
            patch_revision=1,
            reviewer_feedback=task_dict.get('reviewer_feedback')
        )

        # 模板决定走哪条生成路径。
        # 硬约束：auto 且模板没有显式覆盖 model / max_retries 时，必须落模块级 _generator
        # （即 generate_patch）。这样既保持改造前行为，也让注入式测试（直接改
        # patch_generator._generator）继续有效 —— auto 缺省路径绝不自己构造生成器。
        template_model = params.get('patch_model')
        template_retries = params.get('patch_max_retries', 2)
        explicit_override = template_model is not None or template_retries != 2
        provider_choice = params.get('patch_provider', 'auto')
        generator = None
        if provider_choice == 'fake':
            resolved_branch = 'fake'
            generator = FakePatchGenerator('valid')
        elif provider_choice == 'deepseek' or (provider_choice == 'auto' and explicit_override):
            resolved_branch = 'llm'
            generator_kwargs = {'api_key': os.environ.get('DEEPSEEK_API_KEY', ''), 'max_retries': template_retries}
            if template_model:
                generator_kwargs['model'] = template_model
            generator = LLMPatchGenerator(**generator_kwargs)
        else:
            resolved_branch = 'module'

        # Record PATCH_GENERATION_STARTED event
        started_payload = {
            'type': 'PATCH_GENERATION_STARTED',
            'provider': 'pending',
            'patch_revision': ctx.patch_revision,
            'base_commit': ctx.base_commit,
            'plan_id': plan['id'],
            'analysis_attempt': task_row['attempt_number']
        }
        if params:
            # patch 阶段的实际生效值必须落痕：只有 search_trace 的回显不足以证明
            # 模板在补丁阶段也生效了（这一阶段最容易"看起来生效、实际没生效"）。
            started_payload['template_patch'] = {
                'provider': provider_choice,
                'resolved_branch': resolved_branch,
                'model': template_model if resolved_branch == 'llm' else None,
                'max_retries': template_retries if resolved_branch == 'llm' else None,
                'max_changed_lines': effective_max_changed_lines(params)
            }
        await append_event(
            task_id,
            'PATCH_GENERATION_STARTED',
            started_payload,
            'Patch generation started',
            'patch_agent'
        )

        # Generate patch
        result = await generate_patch(ctx) if generator is None else await generator.generate(ctx)

        # Validate the generated patch
        patch_id, check, valid = await validate_and_create_patch(
            task_id,
            task_row['attempt_number'],
            result.diff,
            plan['id'],
            plan['base_commit'],
            plan_json,
            info['git_root'],
            roots,
            max_changed_lines=effective_max_changed_lines(params)
        )

        if valid:
            # 状态推进与事件同事务一次写完（不再单独 append_event，避免重复事件）
            await transition_task(
                task_id,
                to_status=AWAITING_PATCH_APPROVAL,
                event_type='PATCH_GENERATED',
                payload={
                    'type': 'PATCH_GENERATED',
                    'provider': result.provider,
                    'patch_revision': ctx.patch_revision,
                    'base_commit': ctx.base_commit,
                    'plan_id': plan['id'],
                    'analysis_attempt': task_row['attempt_number'],
                    'changed_files': result.changed_files,
                    'validation': check.model_dump(),
                    'prompt_digest': result.prompt_digest,
                    'response_digest': result.response_digest,
                    'risk_notes': result.risk_notes,
                    'patch_id': patch_id
                },
                message='Patch generated and ready for review',
                agent_name='patch_agent'
            )
        else:
            await transition_task(
                task_id,
                to_status=AWAITING_PATCH_GENERATION_RETRY,
                event_type='PATCH_GENERATION_FAILED',
                payload={
                    'type': 'PATCH_GENERATION_FAILED',
                    'provider': result.provider,
                    'patch_revision': ctx.patch_revision,
                    'base_commit': ctx.base_commit,
                    'plan_id': plan['id'],
                    'analysis_attempt': task_row['attempt_number'],
                    'findings': check.findings,
                    'risk_notes': result.risk_notes,
                    'patch_id': patch_id
                },
                message='Patch generation failed, awaiting retry decision',
                agent_name='patch_agent'
            )

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        message = str(exc)[:1000] or exc.__class__.__name__
        await transition_task(
            task_id,
            to_status=FAILED,
            event_type='TASK_FAILED',
            payload={'type': 'TASK_FAILED', 'error': message},
            message='Patch generation system error',
            agent_name='patch_agent',
            error_message=message,
            finished_at=_now()
        )

    finally:
        active_tasks.pop(task_id, None)

async def schedule_generation(task_id: int) -> bool:
    """Schedule patch generation as background task."""
    if task_id in active_tasks:
        return False
    task = asyncio.create_task(run_generate_patch(task_id))
    active_tasks[task_id] = task
    return True

async def recover_zombie_tasks() -> None:
    """On startup: mark any running tasks as failed (zombie recovery)."""
    c = await _conn()
    try:
        await c.execute('BEGIN IMMEDIATE')

        # Find all running tasks
        rows = await (await c.execute(
            'SELECT id, next_event_seq FROM code_tasks WHERE status = ?',
            (RUNNING,)
        )).fetchall()

        for row in rows:
            task_id = row['id']
            next_seq = int(row['next_event_seq'])

            # Update task to FAILED
            await c.execute(
                'UPDATE code_tasks SET status = ?, finished_at = ?, current_agent = ? WHERE id = ?',
                (FAILED, _now(), None, task_id)
            )

            # Record recovery event
            await c.execute(
                'INSERT INTO task_events(task_id, event_seq, event_type, agent_name, message, payload_json) VALUES(?, ?, ?, ?, ?, ?)',
                (task_id, next_seq, 'TASK_RECOVERED', 'system', 'Service startup: recovered zombie task', json.dumps({'type': 'TASK_RECOVERED', 'status': FAILED}, ensure_ascii=False))
            )

            # Update next_event_seq
            await c.execute(
                'UPDATE code_tasks SET next_event_seq = ? WHERE id = ?',
                (next_seq + 1, task_id)
            )

        await c.commit()
    finally:
        await c.close()

