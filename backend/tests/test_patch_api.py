import asyncio
import subprocess

import httpx
import pytest

import db
import task_service
from main import api
from constants import AWAITING_PLAN_APPROVAL, GENERATING_PATCH, AWAITING_PATCH_APPROVAL


@pytest.mark.asyncio
async def test_patch_validation_requires_approved_plan_and_persists_result(monkeypatch, tmp_path):
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
        for _ in range(30):
            current = await client.get(f"/api/tasks/{task_id}")
            if current.json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        blocked = await client.post(f"/api/tasks/{task_id}/patches/validate", json={"diff":"--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"})
        assert blocked.status_code == 409
        approved = await client.post(f"/api/tasks/{task_id}/plan/approve", json={})
        assert approved.status_code == 200
        # Wait for patch generation to complete
        for _ in range(30):
            current = await client.get(f"/api/tasks/{task_id}")
            if current.json()["status"] == AWAITING_PATCH_APPROVAL: break
            await asyncio.sleep(.05)
        if current.json()["status"] == 'failed':
            print(f"Task error: {current.json()['error_message']}")
        assert current.json()["status"] == AWAITING_PATCH_APPROVAL, f"Expected AWAITING_PATCH_APPROVAL, got {current.json()['status']}"
        patch = await client.post(f"/api/tasks/{task_id}/patches/validate", json={"diff":"--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n def validate(value):\n-    return value > 0\n+    return value >= 0\n"})
        assert patch.status_code == 201
        assert patch.json()["changed_files"] == ["src/app.py"]
        assert patch.json()["approved"] is False
        invalid = await client.post(f"/api/tasks/{task_id}/patches/validate", json={"diff":"--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n" + "\n".join(["+too-many"] * 51)})
        assert invalid.status_code == 422
