import asyncio
import subprocess

import httpx
import pytest

import db
import task_service
from main import api
from constants import AWAITING_PLAN_APPROVAL


@pytest.mark.asyncio
async def test_plan_approval_and_rejection_are_audited(monkeypatch, tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n", encoding="utf-8")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        repo = await client.post("/api/repositories", json={"name":"repo", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        task = await client.post("/api/tasks", json={"repository_id":repo.json()["id"], "issue_title":"Add validation", "issue_body":"Validate value before save."})
        task_id = task.json()["id"]
        for _ in range(20):
            if (await client.get(f"/api/tasks/{task_id}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        rejected = await client.post(f"/api/tasks/{task_id}/plan/reject", json={})
        assert rejected.status_code == 422
        rejected = await client.post(f"/api/tasks/{task_id}/plan/reject", json={"reason":"需要更小的修改范围"})
        assert rejected.json()["plan_decision"] == "rejected"
        reanalyzed = await client.post(f"/api/tasks/{task_id}/plan/reanalyze", json={"feedback":"只考虑 validator 文件"})
        assert reanalyzed.status_code == 202
        assert reanalyzed.json()["attempt_number"] == 1
        for _ in range(20):
            current = await client.get(f"/api/tasks/{task_id}")
            if current.json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        assert current.json()["reviewer_feedback"] == "只考虑 validator 文件"
        approved = await client.post(f"/api/tasks/{task_id}/plan/approve", json={"reason":"人工确认最小范围"})
        assert approved.status_code == 200
        assert approved.json()["plan_decision"] == "approved"
        repeated = await client.post(f"/api/tasks/{task_id}/plan/approve", json={})
        assert repeated.status_code == 409
        events = await client.get(f"/api/tasks/{task_id}/events")
        assert events.json()[-1]["event_type"] == "PLAN_APPROVED"


@pytest.mark.asyncio
async def test_plan_approval_override_reason_for_failed_auto_review(monkeypatch, tmp_path):
    """Test 409 when auto_review_passed=0 without reason, then 200 with reason provided."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def auth_check(token):\n    return bool(token)\n", encoding="utf-8")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        repo = await client.post("/api/repositories", json={"name":"repo", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        task = await client.post("/api/tasks", json={"repository_id":repo.json()["id"], "issue_title":"Fix auth token validation", "issue_body":"Auth token validation is broken."})
        task_id = task.json()["id"]
        for _ in range(20):
            if (await client.get(f"/api/tasks/{task_id}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        plan = await task_service.get_current_plan(task_id)
        assert plan["auto_review_passed"] == 0, "fixture 没造出高风险场景，测试无效"
        no_reason = await client.post(f"/api/tasks/{task_id}/plan/approve", json={})
        assert no_reason.status_code == 409
        with_reason = await client.post(f"/api/tasks/{task_id}/plan/approve", json={"reason":"人工确认放行"})
        assert with_reason.status_code == 200
        assert with_reason.json()["plan_decision"] == "approved"
        assert (await task_service.get_plan(plan["id"]))["override_reason"] == "人工确认放行"

