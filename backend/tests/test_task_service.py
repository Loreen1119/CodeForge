import pytest
import db
import task_service

@pytest.mark.asyncio
async def test_event_seq_is_task_local(monkeypatch, tmp_path):
    path = tmp_path / 'tasks.db'
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setattr(task_service, 'DB_PATH', path)
    await db.init_db()
    repo = await task_service.create_repository({'name':'test','local_path':str(tmp_path)})
    task_id = await task_service.create_task_record({'repository_id':repo,'issue_title':'test','issue_body':'test'})
    assert await task_service.append_event(task_id, 'TASK_CREATED', {'type':'TASK_CREATED'}) == 1
    assert (await task_service.list_events(task_id))[0][0] == 1


@pytest.mark.asyncio
async def test_runner_records_failure_when_repository_is_no_longer_available(monkeypatch, tmp_path):
    path = tmp_path / 'tasks.db'
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setattr(task_service, 'DB_PATH', path)
    await db.init_db()
    repo = await task_service.create_repository({'name':'missing','local_path':str(tmp_path / 'missing')})
    task_id = await task_service.create_task_record({'repository_id':repo,'issue_title':'Fix bug','issue_body':'Fix parser.'})
    await task_service.run_task(task_id)
    task = await task_service.get_task(task_id)
    events = await task_service.list_events(task_id)
    assert task['status'] == 'failed'
    assert task['error_message']
    assert events[-1][1] == 'TASK_FAILED'
