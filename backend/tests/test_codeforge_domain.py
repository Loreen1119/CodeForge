import json
import pytest

from db import init_db
from task_service import append_event, create_repository, create_task_record, get_task, list_events

@pytest.mark.asyncio
async def test_repository_task_event_roundtrip(tmp_path, monkeypatch):
    import db, task_service
    path = tmp_path / 'test.db'
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setattr(task_service, 'DB_PATH', path)
    await init_db()
    repository_id = await create_repository({'name': 'demo', 'local_path': str(tmp_path), 'default_branch': 'main'})
    task_id = await create_task_record({'repository_id': repository_id, 'issue_title': 'Fix input validation', 'issue_body': 'Add a boundary check'})
    await append_event(task_id, 'TASK_CREATED', {'type': 'TASK_CREATED', 'status': 'pending'}, 'created')
    row = await get_task(task_id)
    events = await list_events(task_id)
    assert row['repository_id'] == repository_id
    assert row['status'] == 'pending'
    assert events[0][0] == 1
    assert json.loads(json.dumps(events[0][4]))['type'] == 'TASK_CREATED'
