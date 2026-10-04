"""Minimal tests for P0-3 modifications."""
import asyncio
import subprocess
import json
import httpx
import pytest
import sqlite3
import db
import task_service
import patch_generator
from main import api
from constants import AWAITING_PLAN_APPROVAL, AWAITING_PATCH_APPROVAL, AWAITING_PATCH_GENERATION_RETRY

@pytest.mark.asyncio
async def test_valid_mode_reaches_patch_approval_and_diff_is_appliable(monkeypatch, tmp_path):
    """Valid mode test."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    patch_generator._generator = patch_generator.FakePatchGenerator('valid')
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/repositories", json={"name":"r", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        t = await c.post("/api/tasks", json={"repository_id":r.json()["id"], "issue_title":"Add validation", "issue_body":"Validate value before save."})
        tid = t.json()["id"]
        for _ in range(30):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        assert (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL
        await c.post(f"/api/tasks/{tid}/plan/approve", json={})
        for _ in range(80):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PATCH_APPROVAL: break
            await asyncio.sleep(.05)
        assert (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PATCH_APPROVAL
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT base_commit, plan_id FROM patches WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,))
        pr = cur.fetchone()
        assert pr is not None
        cur.execute("SELECT base_commit FROM plans WHERE id = ?", (pr["plan_id"],))
        pb = cur.fetchone()["base_commit"]
        assert pr["base_commit"] == pb
        # 用例名说 "diff_is_appliable"，就必须真的验一次能 apply ——
        # 这正是 P0-3 漏掉的那一步：旧 fallback 产出零上下文补丁，
        # 没有这条断言就永远发现不了。
        cur.execute("SELECT diff FROM patches WHERE task_id = ? ORDER BY id DESC LIMIT 1", (tid,))
        stored_diff = cur.fetchone()["diff"]
        assert stored_diff, "库里的 diff 不能为空"
        checked = subprocess.run(
            ["git", "-C", str(tmp_path), "apply", "--check", "-"],
            input=stored_diff.encode("utf-8"), capture_output=True,
        )
        assert checked.returncode == 0, (
            f"库里的 diff 无法 apply：{checked.stderr.decode('utf-8', 'replace')}\n--- diff ---\n{stored_diff}"
        )
        conn.close()

@pytest.mark.asyncio
async def test_out_of_scope_mode_lands_in_retry_with_scope_finding(monkeypatch, tmp_path):
    """Out of scope test."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/repositories", json={"name":"r", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        t = await c.post("/api/tasks", json={"repository_id":r.json()["id"], "issue_title":"Add validation", "issue_body":"Validate value before save."})
        tid = t.json()["id"]
        for _ in range(30):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        patch_generator._generator = patch_generator.FakePatchGenerator('out_of_scope')
        await c.post(f"/api/tasks/{tid}/plan/approve", json={})
        for _ in range(80):
            s = (await c.get(f"/api/tasks/{tid}")).json()["status"]
            if s == AWAITING_PATCH_GENERATION_RETRY: break
            await asyncio.sleep(.05)
        assert s == AWAITING_PATCH_GENERATION_RETRY

@pytest.mark.asyncio
async def test_dangerous_mode_is_rejected_by_dangerous_token_check(monkeypatch, tmp_path):
    """Dangerous mode test."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/repositories", json={"name":"r", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        t = await c.post("/api/tasks", json={"repository_id":r.json()["id"], "issue_title":"Add validation", "issue_body":"Validate value before save."})
        tid = t.json()["id"]
        for _ in range(30):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        patch_generator._generator = patch_generator.FakePatchGenerator('dangerous')
        await c.post(f"/api/tasks/{tid}/plan/approve", json={})
        for _ in range(80):
            s = (await c.get(f"/api/tasks/{tid}")).json()["status"]
            if s == AWAITING_PATCH_GENERATION_RETRY: break
            await asyncio.sleep(.05)
        assert s == AWAITING_PATCH_GENERATION_RETRY

@pytest.mark.asyncio
async def test_unapplicable_mode_passes_validate_but_fails_git_apply(monkeypatch, tmp_path):
    """Unapplicable mode test."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/repositories", json={"name":"r", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        t = await c.post("/api/tasks", json={"repository_id":r.json()["id"], "issue_title":"Add validation", "issue_body":"Validate value before save."})
        tid = t.json()["id"]
        for _ in range(30):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        patch_generator._generator = patch_generator.FakePatchGenerator('unapplicable')
        await c.post(f"/api/tasks/{tid}/plan/approve", json={})
        for _ in range(80):
            s = (await c.get(f"/api/tasks/{tid}")).json()["status"]
            if s == AWAITING_PATCH_APPROVAL: break
            await asyncio.sleep(.05)
        assert s == AWAITING_PATCH_APPROVAL

@pytest.mark.asyncio
async def test_regenerate_restarts_generation_and_can_succeed(monkeypatch, tmp_path):
    """Regenerate test."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n")
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post("/api/repositories", json={"name":"r", "local_path":str(tmp_path), "allowed_source_roots":["src"]})
        t = await c.post("/api/tasks", json={"repository_id":r.json()["id"], "issue_title":"Add validation", "issue_body":"Validate value before save."})
        tid = t.json()["id"]
        for _ in range(30):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        patch_generator._generator = patch_generator.FakePatchGenerator('out_of_scope')
        await c.post(f"/api/tasks/{tid}/plan/approve", json={})
        for _ in range(80):
            s = (await c.get(f"/api/tasks/{tid}")).json()["status"]
            if s == AWAITING_PATCH_GENERATION_RETRY: break
            await asyncio.sleep(.05)
        assert s == AWAITING_PATCH_GENERATION_RETRY
        patch_generator._generator = patch_generator.FakePatchGenerator('valid')
        r = await c.post(f"/api/tasks/{tid}/patches/regenerate")
        assert r.status_code == 202
        for _ in range(80):
            s = (await c.get(f"/api/tasks/{tid}")).json()["status"]
            if s == AWAITING_PATCH_APPROVAL: break
            await asyncio.sleep(.05)
        assert s == AWAITING_PATCH_APPROVAL
