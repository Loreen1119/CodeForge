import asyncio
import subprocess

import httpx
import pytest

import db
import task_service
from main import api
from constants import AWAITING_PLAN_APPROVAL, AWAITING_PATCH_APPROVAL


@pytest.mark.asyncio
async def test_patch_decision_is_separate_from_application(monkeypatch, tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n", encoding="utf-8")
    db_path=tmp_path / "api.db"
    monkeypatch.setattr(db,"DB_PATH",db_path); monkeypatch.setattr(task_service,"DB_PATH",db_path)
    await db.init_db()
    transport=httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport,base_url="http://test") as client:
        repo=await client.post("/api/repositories",json={"name":"repo","local_path":str(tmp_path),"allowed_source_roots":["src"]})
        task=await client.post("/api/tasks",json={"repository_id":repo.json()["id"],"issue_title":"Add validation","issue_body":"Validate value before save."})
        tid=task.json()["id"]
        for _ in range(30):
            if (await client.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.05)
        await client.post(f"/api/tasks/{tid}/plan/approve",json={})
        # Wait for patch generation to complete
        for _ in range(30):
            if (await client.get(f"/api/tasks/{tid}")).json()["status"] == AWAITING_PATCH_APPROVAL: break
            await asyncio.sleep(.05)
        patch=await client.post(f"/api/tasks/{tid}/patches/validate",json={"diff":"--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n def validate(value):\n-    return value > 0\n+    return value >= 0\n"})
        pid=patch.json()["id"]
        rejected=await client.post(f"/api/tasks/{tid}/patches/{pid}/reject",json={})
        assert rejected.status_code == 422
        approved=await client.post(f"/api/tasks/{tid}/patches/{pid}/approve",json={})
        assert approved.status_code == 200
        assert approved.json()["approved"] is True
        assert approved.json()["apply_status"] == "not_applied"
        again=await client.post(f"/api/tasks/{tid}/patches/{pid}/approve",json={})
        assert again.status_code == 409
