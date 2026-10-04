import asyncio, subprocess
import httpx
import pytest
import db, task_service
from main import api
from constants import AWAITING_PLAN_APPROVAL, AWAITING_PATCH_APPROVAL

@pytest.mark.asyncio
async def test_worktree_apply_and_allowlisted_test_roundtrip(monkeypatch, tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    subprocess.run(["git","-C",str(repo),"init"],check=True,capture_output=True)
    subprocess.run(["git","-C",str(repo),"config","user.email","test@example.com"],check=True)
    subprocess.run(["git","-C",str(repo),"config","user.name","Test"],check=True)
    (repo/"src").mkdir(); (repo/"src"/"app.py").write_text("def validate(value):\n    return value > 0\n")
    subprocess.run(["git","-C",str(repo),"add","."],check=True); subprocess.run(["git","-C",str(repo),"commit","-m","base"],check=True,capture_output=True)
    db_path=tmp_path/"db.sqlite"; monkeypatch.setattr(db,"DB_PATH",db_path); monkeypatch.setattr(task_service,"DB_PATH",db_path); await db.init_db()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api),base_url="http://test") as c:
        r=await c.post("/api/repositories",json={"name":"r","local_path":str(repo),"allowed_source_roots":["src"],"allowed_test_commands":["git status --porcelain"]}); assert r.status_code==201
        t=await c.post("/api/tasks",json={"repository_id":r.json()["id"],"issue_title":"fix","issue_body":"validate"}); tid=t.json()["id"]
        for _ in range(50):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"]==AWAITING_PLAN_APPROVAL: break
            await asyncio.sleep(.02)
        assert (await c.get(f"/api/tasks/{tid}")).json()["status"]==AWAITING_PLAN_APPROVAL
        assert (await c.post(f"/api/tasks/{tid}/plan/approve",json={"reason":"manual approval"})).status_code==200
        # Wait for patch generation to complete
        for _ in range(50):
            if (await c.get(f"/api/tasks/{tid}")).json()["status"]==AWAITING_PATCH_APPROVAL: break
            await asyncio.sleep(.02)
        assert (await c.get(f"/api/tasks/{tid}")).json()["status"]==AWAITING_PATCH_APPROVAL
        diff="--- a/src/app.py\n+++ b/src/app.py\n@@ -1,2 +1,2 @@\n def validate(value):\n-    return value > 0\n+    return value >= 0\n"
        p=await c.post(f"/api/tasks/{tid}/patches/validate",json={"diff":diff}); assert p.status_code==201; pid=p.json()["id"]
        assert (await c.post(f"/api/tasks/{tid}/patches/{pid}/approve",json={})).status_code==200
        assert (await c.post(f"/api/tasks/{tid}/patches/{pid}/apply")).status_code==200
        tr=await c.post(f"/api/tasks/{tid}/patches/{pid}/tests",json={"command":"git status --porcelain"}); assert tr.status_code==201
        assert tr.json()["status"]=="passed"
        runs=await c.get(f"/api/tasks/{tid}/test-runs"); assert len(runs.json())==1
