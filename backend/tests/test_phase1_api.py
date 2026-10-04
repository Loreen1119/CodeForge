import asyncio

import httpx
import pytest
import subprocess

import db
import task_service
from main import api
from constants import AWAITING_PLAN_APPROVAL, NEEDS_CONTEXT

# SSE 读取必须能结束：任务进入任何等待人工的终态（awaiting_approval / needs_context）
# 时流都要关闭。给一个硬超时，避免流不关闭时把整个测试套件挂死而不是失败。
SSE_TIMEOUT_SECONDS = 15

SOURCE_WITH_HITS = "def validation_check(value):\n    return value > 0\n"


def _init_repo(tmp_path, source=None):
    subprocess.run(['git', 'init', str(tmp_path)], check=True, capture_output=True)
    (tmp_path / 'src').mkdir()
    if source is not None:
        (tmp_path / 'src' / 'service.py').write_text(source, encoding='utf-8')


async def _create_task(client, repository_id, title='Fix validation', body='Add a boundary check'):
    created = await client.post('/api/tasks', json={
        'repository_id': repository_id,
        'issue_title': title,
        'issue_body': body,
    })
    assert created.status_code == 202
    return created.json()['id']


async def _wait_for_status(client, task_id, *statuses, attempts=40):
    current = None
    for _ in range(attempts):
        current = await client.get(f'/api/tasks/{task_id}')
        if current.json()['status'] in statuses:
            return current
        await asyncio.sleep(0.05)
    return current


@pytest.mark.asyncio
async def test_repository_task_sse_and_replay(monkeypatch, tmp_path):
    """上下文不足时任务停在 needs_context，SSE 必须关闭并能完整重放事件。"""
    _init_repo(tmp_path)
    path = tmp_path / 'api.db'
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setattr(task_service, 'DB_PATH', path)
    await db.init_db()

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        repository = await client.post('/api/repositories', json={
            'name': 'sample', 'local_path': str(tmp_path), 'default_branch': 'main', 'allowed_source_roots': ['src'],
        })
        assert repository.status_code == 201
        repository_id = repository.json()['id']

        task_id = await _create_task(client, repository_id)
        current = await _wait_for_status(client, task_id, 'needs_context')
        assert current.json()['status'] == 'needs_context'

        stream = await asyncio.wait_for(
            client.get(f'/api/tasks/{task_id}/stream'), timeout=SSE_TIMEOUT_SECONDS
        )
        assert stream.status_code == 200
        for event in ('TASK_CREATED', 'TASK_STARTED', 'REQUIREMENT_ANALYZED', 'CODE_SEARCHED',
                      'PLAN_DRAFTED', 'PLAN_REVIEWED', 'NEEDS_CONTEXT'):
            assert event in stream.text
        assert 'TASK_READY' not in stream.text

        replay = await client.get(f'/api/tasks/{task_id}/events')
        assert replay.status_code == 200
        event_types = [event['event_type'] for event in replay.json()]
        assert event_types == ['TASK_CREATED', 'TASK_STARTED', 'REQUIREMENT_ANALYZED', 'CODE_SEARCHED',
                               'PLAN_DRAFTED', 'PLAN_REVIEWED', 'NEEDS_CONTEXT']
        assert replay.json()[2]['payload']['analysis']['task_type'] == 'validation'


@pytest.mark.asyncio
async def test_repository_task_sse_ends_with_task_ready_when_context_found(monkeypatch, tmp_path):
    """检索有命中时任务进入 awaiting_approval，SSE 同样必须关闭。"""
    _init_repo(tmp_path, source=SOURCE_WITH_HITS)
    path = tmp_path / 'api.db'
    monkeypatch.setattr(db, 'DB_PATH', path)
    monkeypatch.setattr(task_service, 'DB_PATH', path)
    await db.init_db()

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        repository = await client.post('/api/repositories', json={
            'name': 'sample', 'local_path': str(tmp_path), 'default_branch': 'main', 'allowed_source_roots': ['src'],
        })
        assert repository.status_code == 201
        repository_id = repository.json()['id']

        task_id = await _create_task(client, repository_id)
        current = await _wait_for_status(client, task_id, AWAITING_PLAN_APPROVAL)
        assert current.json()['status'] == AWAITING_PLAN_APPROVAL

        stream = await asyncio.wait_for(
            client.get(f'/api/tasks/{task_id}/stream'), timeout=SSE_TIMEOUT_SECONDS
        )
        assert stream.status_code == 200
        assert 'TASK_READY' in stream.text
        assert 'NEEDS_CONTEXT' not in stream.text

        replay = await client.get(f'/api/tasks/{task_id}/events')
        assert replay.status_code == 200
        event_types = [event['event_type'] for event in replay.json()]
        assert event_types[-1] == 'TASK_READY'
        searched = next(event for event in replay.json() if event['event_type'] == 'CODE_SEARCHED')
        assert searched['payload']['results'], '检索有命中时必须带上结果，否则方案等于凭空生成'
