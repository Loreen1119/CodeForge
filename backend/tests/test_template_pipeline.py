"""模板参数真正流进五步链路：对拍、锚点、检索预算、provider 路由、收窄边界、测试端点。

口径说明：`template_id` / `template_applied` / `template_patch` 属**观测元数据**，
不计入"产出对拍"—— 对拍比的是 analysis / results / plan / review 这些真实产物。
"""
import json
import subprocess

import httpx
import pytest

import db
import main
import patch_generator
import task_service
from agent_graph import analyze_requirement, run_code_search_node
from patch_generator import FakePatchGenerator
from schemas import TemplateParams

ISSUE_TITLE = "Add validation"
ISSUE_BODY = "Validate value before save."


async def _client(monkeypatch, tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text("def validate(value):\n    return value > 0\n", encoding="utf-8")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # task_service 用的是 `from db import DB_PATH` 的模块级副本，只改 db.DB_PATH 会静默
    # 把数据写进真实 codeforge.db。两个都要改。
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    # 后台调度换成空实现：这里显式 await 各个阶段，链路才可复现。
    async def _no_background(task_id):
        return True

    monkeypatch.setattr(main, "schedule_task", _no_background)
    monkeypatch.setattr(main, "schedule_generation", _no_background)
    # 缺省生成器固定成 Fake：不让用例行为取决于本机有没有 DEEPSEEK_API_KEY。
    monkeypatch.setattr(patch_generator, "_generator", FakePatchGenerator("valid"))

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.api), base_url="http://test")
    return client, db_path


async def _repository(client, tmp_path) -> int:
    created = await client.post("/api/repositories", json={
        "name": "sample", "local_path": str(tmp_path), "allowed_source_roots": ["src"],
        "allowed_test_commands": ["pytest -q"],
    })
    assert created.status_code == 201, created.text
    return created.json()["id"]


async def _new_task(client, repository_id, *, template_id=None, title=ISSUE_TITLE, body=ISSUE_BODY) -> dict:
    payload = {"repository_id": repository_id, "issue_title": title, "issue_body": body}
    if template_id is not None:
        payload["template_id"] = template_id
    response = await client.post("/api/tasks", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


async def _template(client, name, params=None) -> dict:
    body = {"name": name}
    if params is not None:
        body["params"] = params
    response = await client.post("/api/templates", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _event(events, event_type):
    matches = [item for item in events if item[1] == event_type]
    assert matches, f"缺少 {event_type} 事件"
    return matches[-1][4]


async def _drive_to_patch_stage(client, task_id):
    """显式跑完：需求/检索/方案 → 批准 → 生成补丁。"""
    await task_service.run_task(task_id)
    approve = await client.post(f"/api/tasks/{task_id}/plan/approve", json={})
    assert approve.status_code == 200, approve.text
    await task_service.run_generate_patch(task_id)
    return (await client.get(f"/api/tasks/{task_id}")).json()


def _single_file_diff(path: str, added_lines: int) -> str:
    """一份除"变更行数"外全部合法的单文件补丁。"""
    body = "".join(f"+added_{index}\n" for index in range(added_lines))
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,{added_lines + 1} @@\n def validate(value):\n{body}"


@pytest.mark.asyncio
async def test_default_template_output_matches_no_template(monkeypatch, tmp_path):
    """对拍：不传模板 与 传全默认模板，四个节点的真实产出逐项相同。"""
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        plain = await _new_task(client, repository_id)
        full_default = await _template(client, "全默认")
        templated = await _new_task(client, repository_id, template_id=full_default["id"])

        await task_service.run_task(plain["id"])
        await task_service.run_task(templated["id"])

        plain_events = await task_service.list_events(plain["id"])
        templated_events = await task_service.list_events(templated["id"])

        plain_requirement = _event(plain_events, "REQUIREMENT_ANALYZED")
        templated_requirement = _event(templated_events, "REQUIREMENT_ANALYZED")
        assert plain_requirement["analysis"] == templated_requirement["analysis"]
        assert "template_id" not in plain_requirement  # 无模板不加键
        assert templated_requirement["template_id"] == full_default["id"]

        plain_search = _event(plain_events, "CODE_SEARCHED")
        templated_search = _event(templated_events, "CODE_SEARCHED")
        assert plain_search["results"], "对拍前提：这条 Issue 必须真的检索到源码"
        assert plain_search["results"] == templated_search["results"]

        assert not [item for item in plain_search["strategies"] if item.get("strategy") == "template_applied"]
        applied = [item for item in templated_search["strategies"] if item.get("strategy") == "template_applied"]
        assert len(applied) == 1
        assert applied[0]["params"] == TemplateParams().model_dump()

        assert _event(plain_events, "PLAN_DRAFTED")["plan"] == _event(templated_events, "PLAN_DRAFTED")["plan"]
        assert _event(plain_events, "PLAN_REVIEWED")["review"] == _event(templated_events, "PLAN_REVIEWED")["review"]

        assert (await client.get(f"/api/tasks/{plain['id']}")).json()["status"] == "awaiting_plan_approval"
        assert (await client.get(f"/api/tasks/{templated['id']}")).json()["status"] == "awaiting_plan_approval"


@pytest.mark.asyncio
async def test_anchor_paths_supply_context_when_keywords_miss(monkeypatch, tmp_path):
    """锚点：措辞与源码完全不重叠时，只有锚点能救回上下文，且状态因此不同。"""
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        title, body = "保存时报错", "保存时出现空指针，需要加保护。"
        plain = await _new_task(client, repository_id, title=title, body=body)
        anchored_template = await _template(client, "带锚点", {"anchor_paths": ["src/service.py"]})
        anchored = await _new_task(client, repository_id, template_id=anchored_template["id"], title=title, body=body)

        await task_service.run_task(plain["id"])
        await task_service.run_task(anchored["id"])

        assert _event(await task_service.list_events(plain["id"]), "CODE_SEARCHED")["results"] == []
        anchored_search = _event(await task_service.list_events(anchored["id"]), "CODE_SEARCHED")
        assert [item["path"] for item in anchored_search["results"]] == ["src/service.py"]
        strategies = {item["strategy"] for item in anchored_search["strategies"]}
        assert {"template_anchors", "template_applied"} <= strategies

        assert (await client.get(f"/api/tasks/{plain['id']}")).json()["status"] == "needs_context"
        assert (await client.get(f"/api/tasks/{anchored['id']}")).json()["status"] == "awaiting_plan_approval"


def test_search_budget_and_result_limit_come_from_template(tmp_path):
    """检索预算三项参数直接决定命中数量，缺省值与改造前的字面量一致。

    自 2026-10-17 起检索按「文件」去重（同文件多行命中同一关键词只保留一条），
    所以用「不同文件」而非「同文件多行」来构造命中数，避免把文件去重误当成 bug。
    """
    source = tmp_path / "src"
    source.mkdir()
    for idx in range(4):
        (source / f"alpha_{idx}.py").write_text(f"alpha_{idx} = {idx}\n", encoding="utf-8")
    for idx in range(4):
        (source / f"beta_{idx}.py").write_text(f"beta_{idx} = {idx}\n", encoding="utf-8")

    def _search(**params):
        state = {"task_id": 1, "repository_id": 1, "issue_title": "alpha beta", "issue_body": "alpha beta"}
        state["requirement"] = analyze_requirement("alpha beta", "alpha beta")
        if params:
            state["template_params"] = params
        return run_code_search_node(state, str(tmp_path), ["src"])

    assert len(_search()["code_context"]) == 8                      # 两个关键词各 4 个文件 = 缺省上限 8
    assert len(_search(search_keyword_budget=1)["code_context"]) == 4          # 只用第一个关键词
    assert len(_search(per_keyword_max_results=1)["code_context"]) == 2        # 每词 1 文件 × 两个关键词
    assert len(_search(per_keyword_max_results=1, search_result_limit=1)["code_context"]) == 1  # 总量再收


def test_analyze_requirement_reads_template_params():
    """需求分析五项参数：强制类型、模板项优先、风险词命中才留痕。"""
    title, body = "修复保存", "保存时出现空指针。"
    baseline = analyze_requirement(title, body)
    assert baseline == analyze_requirement(title, body, None) == analyze_requirement(title, body, {})

    forced = analyze_requirement(title, body, {"task_type": "validation"})
    assert forced.task_type == "validation"
    assert forced.objective == baseline.objective

    prioritised = analyze_requirement(title, body, {"keywords": ["normalise"], "max_keywords": 2})
    assert prioritised.keywords[0] == "normalise" and len(prioritised.keywords) == 2

    criteria = analyze_requirement(title, body, {"acceptance_criteria": ["必须拒绝全角@"]})
    assert criteria.acceptance_criteria[0] == "必须拒绝全角@"

    hit = analyze_requirement("修复", "处理 token 过期", {"risk_keywords": ["token"]})
    assert hit.risk_level == "high"
    assert any("命中模板风险词" in note and "token" in note for note in hit.risk_notes)

    missed = analyze_requirement("修复", "处理 token 过期", {"risk_keywords": ["这个词不会出现"]})
    assert missed.risk_notes == analyze_requirement("修复", "处理 token 过期").risk_notes


@pytest.mark.asyncio
async def test_fake_provider_bypasses_module_generator(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        template = await _template(client, "强制 fake", {"patch_provider": "fake"})
        task = await _new_task(client, repository_id, template_id=template["id"])

        class _Sentinel:
            async def generate(self, ctx):
                raise AssertionError("模板指定 fake 时不应落到模块级生成器")

        monkeypatch.setattr(patch_generator, "_generator", _Sentinel())

        status = await _drive_to_patch_stage(client, task["id"])
        assert status["status"] == "awaiting_patch_approval"

        events = await task_service.list_events(task["id"])
        template_patch = _event(events, "PATCH_GENERATION_STARTED")["template_patch"]
        assert template_patch["resolved_branch"] == "fake" and template_patch["model"] is None
        assert _event(events, "PATCH_GENERATED")["provider"] == "fake"


@pytest.mark.asyncio
async def test_deepseek_provider_uses_template_model_and_retries(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        template = await _template(client, "指定模型", {
            "patch_provider": "deepseek", "patch_model": "glm-test", "patch_max_retries": 4,
        })
        task = await _new_task(client, repository_id, template_id=template["id"])

        captured = []

        class _SpyGenerator:
            def __init__(self, **kwargs):
                captured.append(kwargs)
                self._inner = FakePatchGenerator("valid")

            async def generate(self, ctx):
                return await self._inner.generate(ctx)

        monkeypatch.setattr(task_service, "LLMPatchGenerator", _SpyGenerator)

        status = await _drive_to_patch_stage(client, task["id"])
        assert status["status"] == "awaiting_patch_approval"
        assert captured and captured[-1]["model"] == "glm-test" and captured[-1]["max_retries"] == 4

        events = await task_service.list_events(task["id"])
        template_patch = _event(events, "PATCH_GENERATION_STARTED")["template_patch"]
        assert template_patch["resolved_branch"] == "llm" and template_patch["max_retries"] == 4


@pytest.mark.asyncio
async def test_auto_without_template_still_uses_module_generator(monkeypatch, tmp_path):
    """硬约束回归：无模板的 auto 路径必须继续落模块级 _generator。"""
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        task = await _new_task(client, repository_id)

        class _Sentinel:
            async def generate(self, ctx):
                raise RuntimeError("哨兵：模块级生成器被调用")

        monkeypatch.setattr(patch_generator, "_generator", _Sentinel())

        status = await _drive_to_patch_stage(client, task["id"])
        assert status["status"] == "failed"
        assert "哨兵" in (status["error_message"] or "")

        started = _event(await task_service.list_events(task["id"]), "PATCH_GENERATION_STARTED")
        assert "template_patch" not in started  # 无模板时不加键


@pytest.mark.asyncio
async def test_max_changed_lines_narrows_generation_and_manual_validate(monkeypatch, tmp_path):
    """收窄边界：生成路径与手动提交路径都必须收到模板的 max_changed_lines。"""
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        template = await _template(client, "限 3 行", {"max_changed_lines": 3})
        task = await _new_task(client, repository_id, template_id=template["id"])

        seen = []
        real_validate = task_service.validate_patch

        def _spy(diff, git_root, allowed_roots=None, max_changed_lines=50):
            seen.append(max_changed_lines)
            return real_validate(diff, git_root, allowed_roots, max_changed_lines=max_changed_lines)

        monkeypatch.setattr(task_service, "validate_patch", _spy)

        status = await _drive_to_patch_stage(client, task["id"])
        assert status["status"] == "awaiting_patch_approval"
        assert seen == [3]  # 生成路径确实拿到了模板的收窄值，而不是默认 50
        started = _event(await task_service.list_events(task["id"]), "PATCH_GENERATION_STARTED")
        assert started["template_patch"]["max_changed_lines"] == 3

        # 手动提交路径同样受限：5 行变更 > 模板的 3 行
        rejected = await client.post(
            f"/api/tasks/{task['id']}/patches/validate",
            json={"diff": _single_file_diff("src/service.py", 5)},
        )
        assert rejected.status_code == 422, rejected.text
        assert any("行数" in item for item in rejected.json()["detail"]["findings"])


@pytest.mark.asyncio
async def test_test_endpoint_falls_back_to_template_command_and_timeout(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        template = await _template(client, "带测试命令", {"test_command": "pytest -q", "test_timeout_seconds": 42})
        task = await _new_task(client, repository_id, template_id=template["id"])
        assert (await _drive_to_patch_stage(client, task["id"]))["status"] == "awaiting_patch_approval"
        patch_id = _event(await task_service.list_events(task["id"]), "PATCH_GENERATED")["patch_id"]

        calls = []

        def _record(command, worktree, allowed_commands=None, timeout_seconds=300):
            calls.append((command, timeout_seconds))
            return {"status": "passed", "exit_code": 0, "output": "", "timed_out": False,
                    "environment_error": None, "duration_ms": 1}

        monkeypatch.setattr(main, "run_allowlisted", _record)

        accepted = await client.post(f"/api/tasks/{task['id']}/patches/{patch_id}/tests", json={})
        assert accepted.status_code == 201, accepted.text
        assert calls == [("pytest -q", 42)]

        blocked = await client.post(
            f"/api/tasks/{task['id']}/patches/{patch_id}/tests", json={"command": "not-in-whitelist"}
        )
        assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_test_endpoint_without_command_or_template_is_rejected(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _repository(client, tmp_path)
        task = await _new_task(client, repository_id)
        assert (await _drive_to_patch_stage(client, task["id"]))["status"] == "awaiting_patch_approval"
        patch_id = _event(await task_service.list_events(task["id"]), "PATCH_GENERATED")["patch_id"]

        response = await client.post(f"/api/tasks/{task['id']}/patches/{patch_id}/tests", json={})
        assert response.status_code == 422
