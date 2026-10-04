"""模板 API：能存 / 能读 / 能被下一次任务调用。

锁三件事：
1. 模板 CRUD 走通，越界参数在入库前就被 422 拦住；
2. 用模板建任务时，收敛后的完整参数快照落进 code_tasks（可复现的依据）；
3. 模板的测试命令越出仓库白名单时，建任务失败且不留下半成品任务。
"""
import json
import sqlite3
import subprocess

import httpx
import pytest

import db
import main
import task_service
from schemas import TemplateParams


async def _client(monkeypatch, tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "def validate(value):\n    return value > 0\n", encoding="utf-8"
    )
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # task_service 用的是 `from db import DB_PATH`（模块级副本），只改 db.DB_PATH 不会生效，
    # 会静默把测试数据写进真实 codeforge.db。两个都要改。
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    # 这些用例只管模板契约，不跑真实链路：把后台调度换成空实现。
    async def _no_background(task_id):
        return True

    monkeypatch.setattr(main, "schedule_task", _no_background)
    monkeypatch.setattr(main, "schedule_generation", _no_background)

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.api), base_url="http://test")
    return client, db_path


def _table_count(db_path, table: str) -> int:
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    finally:
        connection.close()


async def _register_repository(client, tmp_path, db_path) -> int:
    created = await client.post(
        "/api/repositories",
        json={"name": "sample", "local_path": str(tmp_path), "allowed_source_roots": ["src"],
              "allowed_test_commands": ["pytest -q"]},
    )
    assert created.status_code == 201, created.text
    # 防呆：仓库确实落进了临时库，而不是真实 codeforge.db
    assert _table_count(db_path, "repositories") == 1
    return created.json()["id"]


@pytest.mark.asyncio
async def test_template_crud_round_trip(monkeypatch, tmp_path):
    client, _ = await _client(monkeypatch, tmp_path)
    async with client:
        created = await client.post(
            "/api/templates",
            json={"name": "标准链路", "params": {"max_changed_lines": 20, "search_keyword_budget": 6}},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["name"] == "标准链路"
        assert body["params"]["max_changed_lines"] == 20
        assert body["params"]["test_timeout_seconds"] == 300  # 没写的项走链路默认值
        assert body["use_count"] == 0

        listed = (await client.get("/api/templates")).json()
        assert [item["id"] for item in listed] == [body["id"]]

        detail = await client.get(f"/api/templates/{body['id']}")
        assert detail.status_code == 200
        assert detail.json()["params"]["search_keyword_budget"] == 6

        assert (await client.delete(f"/api/templates/{body['id']}")).status_code == 204
        assert (await client.get(f"/api/templates/{body['id']}")).status_code == 404
        assert (await client.delete(f"/api/templates/{body['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_out_of_bound_params_and_duplicate_name_are_rejected(monkeypatch, tmp_path):
    client, _ = await _client(monkeypatch, tmp_path)
    async with client:
        too_loose = await client.post("/api/templates", json={"name": "越界", "params": {"max_changed_lines": 80}})
        assert too_loose.status_code == 422
        assert (await client.get("/api/templates")).json() == []  # 没留下半条记录

        assert (await client.post("/api/templates", json={"name": "重名"})).status_code == 201
        assert (await client.post("/api/templates", json={"name": "重名"})).status_code == 400

        assert (await client.post("/api/templates", json={"name": "绑仓库", "repository_id": 999})).status_code == 404


@pytest.mark.asyncio
async def test_task_from_template_records_full_params_snapshot(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _register_repository(client, tmp_path, db_path)
        template = (await client.post("/api/templates", json={
            "name": "同类修复",
            "params": {"task_type": "bug_fix", "keywords": ["normalise"], "anchor_paths": ["src/service.py"],
                       "test_command": "pytest -q", "max_changed_lines": 20},
        })).json()

        created = await client.post("/api/tasks", json={
            "repository_id": repository_id, "issue_title": "规整全角@", "issue_body": "把全角@ 换成半角",
            "template_id": template["id"],
        })
        assert created.status_code == 202, created.text
        task = created.json()
        assert task["template_id"] == template["id"]

        events = (await client.get(f"/api/tasks/{task['id']}/events")).json()
        assert events[0]["event_type"] == "TASK_CREATED"
        assert events[0]["payload"]["template_id"] == template["id"]
        assert events[0]["payload"]["template_name"] == "同类修复"

        connection = sqlite3.connect(str(db_path))
        try:
            stored = connection.execute(
                "SELECT template_id, template_params_json FROM code_tasks WHERE id=?", (task["id"],)
            ).fetchone()
        finally:
            connection.close()

        assert stored[0] == template["id"]
        snapshot = json.loads(stored[1])
        assert set(snapshot) == set(TemplateParams.model_fields)  # 快照是完整参数，不是空对象
        assert snapshot["test_command"] == "pytest -q" and snapshot["max_changed_lines"] == 20
        assert snapshot["search_keyword_budget"] == 8              # 模板没写的回落到默认
        assert snapshot["patch_provider"] == "auto"

        assert (await client.get(f"/api/templates/{template['id']}")).json()["use_count"] == 1


@pytest.mark.asyncio
async def test_task_rejects_template_command_outside_repository_whitelist(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _register_repository(client, tmp_path, db_path)
        template = (await client.post("/api/templates", json={
            "name": "野命令", "params": {"test_command": "rm -rf /"},
        })).json()

        rejected = await client.post("/api/tasks", json={
            "repository_id": repository_id, "issue_title": "x", "issue_body": "y",
            "template_id": template["id"],
        })
        assert rejected.status_code == 422
        assert "白名单" in rejected.json()["detail"]
        assert (await client.get("/api/tasks")).json() == []                   # 不留下半成品任务
        assert (await client.get(f"/api/templates/{template['id']}")).json()["use_count"] == 0

        missing = await client.post("/api/tasks", json={
            "repository_id": repository_id, "issue_title": "x", "issue_body": "y", "template_id": 999,
        })
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_template_can_be_distilled_from_a_finished_task(monkeypatch, tmp_path):
    client, db_path = await _client(monkeypatch, tmp_path)
    async with client:
        repository_id = await _register_repository(client, tmp_path, db_path)
        origin = (await client.post("/api/templates", json={
            "name": "原始模板",
            "params": {"keywords": ["validate"], "anchor_symbols": ["validate"]},
        })).json()
        task = (await client.post("/api/tasks", json={
            "repository_id": repository_id, "issue_title": "校验入参", "issue_body": "加边界校验",
            "template_id": origin["id"],
        })).json()

        distilled = await client.post(f"/api/templates/from-task/{task['id']}", params={"name": "校验入参 · 复刻"})
        assert distilled.status_code == 201, distilled.text
        body = distilled.json()
        assert body["name"] == "校验入参 · 复刻"
        assert body["source_task_id"] == task["id"]
        assert body["repository_id"] is None                    # 沉淀出的是全局模板
        assert body["params"]["keywords"] == ["validate"]
        assert body["params"]["anchor_symbols"] == ["validate"]

        auto = await client.post(f"/api/templates/from-task/{task['id']}")
        assert auto.status_code == 201
        assert auto.json()["name"].endswith("· 沉淀")
        assert (await client.post(f"/api/templates/from-task/{task['id']}")).status_code == 400
        assert (await client.post("/api/templates/from-task/99999")).status_code == 404
