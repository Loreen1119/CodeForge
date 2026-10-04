"""Test P0-4: Cancel API, startup recovery, and idempotent scheduling."""
import asyncio
import subprocess
import httpx
import pytest
import db
import task_service
from main import api
from constants import RUNNING, CANCELLED, FAILED, PENDING


@pytest.mark.asyncio
async def test_cancel_running_task_transitions_to_cancelled(monkeypatch, tmp_path):
    """Test that cancelling a running task transitions it to cancelled status with event."""
    # Setup test database
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    # Create a test task and manually set it to RUNNING
    conn = await task_service._conn()
    try:
        cur = await conn.execute(
            "INSERT INTO repositories(name, local_path, allowed_source_roots, allowed_test_commands) VALUES(?, ?, ?, ?)",
            ("test_repo", "/tmp/test", "[]", "[]")
        )
        await conn.commit()
        repo_id = cur.lastrowid

        cur = await conn.execute(
            "INSERT INTO code_tasks(repository_id, issue_title, issue_body, status) VALUES(?, ?, ?, ?)",
            (repo_id, "Test task", "Test body", RUNNING)
        )
        await conn.commit()
        task_id = cur.lastrowid
    finally:
        await conn.close()

    # Use the API to cancel the task
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/tasks/{task_id}/cancel")
        assert response.status_code == 200

        # Verify status is CANCELLED
        task = response.json()
        assert task["status"] == CANCELLED
        assert task["finished_at"] is not None

        # Verify event was recorded
        events = await client.get(f"/api/tasks/{task_id}/events")
        event_types = [e["event_type"] for e in events.json()]
        assert "TASK_CANCELLED" in event_types


@pytest.mark.asyncio
async def test_cancel_non_running_task_returns_409(monkeypatch, tmp_path):
    """Test that cancelling a non-running task returns 409."""
    # Setup test database
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    # Create a pending task
    conn = await task_service._conn()
    try:
        cur = await conn.execute(
            "INSERT INTO repositories(name, local_path, allowed_source_roots, allowed_test_commands) VALUES(?, ?, ?, ?)",
            ("test_repo", "/tmp/test", "[]", "[]")
        )
        await conn.commit()
        repo_id = cur.lastrowid

        cur = await conn.execute(
            "INSERT INTO code_tasks(repository_id, issue_title, issue_body, status) VALUES(?, ?, ?, ?)",
            (repo_id, "Test task", "Test body", PENDING)
        )
        await conn.commit()
        task_id = cur.lastrowid
    finally:
        await conn.close()

    # Try to cancel a pending task
    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/tasks/{task_id}/cancel")
        assert response.status_code == 409
        assert "running" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_cancel_nonexistent_task_returns_404(monkeypatch, tmp_path):
    """Test that cancelling a nonexistent task returns 404."""
    # Setup test database
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(f"/api/tasks/99999/cancel")
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_startup_recovery_marks_zombie_tasks_as_failed(monkeypatch, tmp_path):
    """Test that recover_zombie_tasks() marks running tasks as failed on startup."""
    # Setup test database
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    # Create multiple tasks with different statuses
    conn = await task_service._conn()
    try:
        cur = await conn.execute(
            "INSERT INTO repositories(name, local_path, allowed_source_roots, allowed_test_commands) VALUES(?, ?, ?, ?)",
            ("test_repo", "/tmp/test", "[]", "[]")
        )
        await conn.commit()
        repo_id = cur.lastrowid

        # Create tasks with different statuses
        running_ids = []
        for i in range(2):
            cur = await conn.execute(
                "INSERT INTO code_tasks(repository_id, issue_title, issue_body, status) VALUES(?, ?, ?, ?)",
                (repo_id, f"Running task {i}", f"Body {i}", RUNNING)
            )
            await conn.commit()
            running_ids.append(cur.lastrowid)

        # Also create a pending task that should not be affected
        cur = await conn.execute(
            "INSERT INTO code_tasks(repository_id, issue_title, issue_body, status) VALUES(?, ?, ?, ?)",
            (repo_id, "Pending task", "Body", PENDING)
        )
        await conn.commit()
        pending_id = cur.lastrowid
    finally:
        await conn.close()

    # Verify tasks are in RUNNING state before recovery
    for task_id in running_ids:
        task = await task_service.get_task(task_id)
        assert task["status"] == RUNNING

    # Call recovery function
    await task_service.recover_zombie_tasks()

    # Verify running tasks are now marked as FAILED
    for task_id in running_ids:
        task = await task_service.get_task(task_id)
        assert task["status"] == FAILED
        assert task["finished_at"] is not None

        # Verify TASK_RECOVERED event was recorded
        events = await task_service.list_events(task_id)
        event_types = [e[1] for e in events]
        assert "TASK_RECOVERED" in event_types

    # Verify pending task was not affected
    pending_task = await task_service.get_task(pending_id)
    assert pending_task["status"] == PENDING


@pytest.mark.asyncio
async def test_idempotent_scheduling(monkeypatch, tmp_path):
    """Test that schedule_task is idempotent - multiple calls don't create multiple tasks."""
    # Setup test database
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    # Create a task
    conn = await task_service._conn()
    try:
        cur = await conn.execute(
            "INSERT INTO repositories(name, local_path, allowed_source_roots, allowed_test_commands) VALUES(?, ?, ?, ?)",
            ("test_repo", "/tmp/test", "[]", "[]")
        )
        await conn.commit()
        repo_id = cur.lastrowid

        cur = await conn.execute(
            "INSERT INTO code_tasks(repository_id, issue_title, issue_body, status) VALUES(?, ?, ?, ?)",
            (repo_id, "Test task", "Test body", PENDING)
        )
        await conn.commit()
        task_id = cur.lastrowid
    finally:
        await conn.close()

    # Schedule the same task multiple times
    result1 = await task_service.schedule_task(task_id)
    assert result1 is True, "First schedule should succeed"

    result2 = await task_service.schedule_task(task_id)
    assert result2 is False, "Second schedule should fail due to idempotency"

    result3 = await task_service.schedule_task(task_id)
    assert result3 is False, "Third schedule should also fail"

    # Verify only one task is active
    assert len(task_service.active_tasks) == 1
    assert task_id in task_service.active_tasks

    # Clean up
    await task_service.shutdown_unfinished_tasks()

