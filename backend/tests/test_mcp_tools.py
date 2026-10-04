"""MCP 工具面：五个能力单元的边界规则。

这一层最容易出的问题不是功能不work，而是**边界被参数放宽**。所以用例集中在
「越界必须被拒」上：自由路径、非白名单命令、参数超范围、以及最危险的一条 ——
不在原始工作区里执行命令。
"""
import json
import sqlite3
import subprocess

import pytest

import db
import mcp_server
import task_service


async def _setup(monkeypatch, tmp_path, *, commands=("pytest -q",)):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "def validate(value):\n    return value > 0\n", encoding="utf-8"
    )
    (tmp_path / "outside.py").write_text("SECRET = 1\n", encoding="utf-8")

    db_path = tmp_path / "mcp.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    # task_service 用的是 `from db import DB_PATH` 的模块级副本，两个都要改。
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    connection = sqlite3.connect(str(db_path))
    try:
        cursor = connection.execute(
            "INSERT INTO repositories(name,local_path,default_branch,allowed_source_roots,allowed_test_commands)"
            " VALUES(?,?,?,?,?)",
            ("sample", str(tmp_path), "main", json.dumps(["src"]), json.dumps(list(commands))),
        )
        connection.commit()
        repository_id = cursor.lastrowid
    finally:
        connection.close()
    return repository_id, db_path


@pytest.mark.asyncio
async def test_list_repositories_exposes_boundaries_but_never_absolute_path(monkeypatch, tmp_path):
    repository_id, _ = await _setup(monkeypatch, tmp_path)

    result = await mcp_server.list_repositories()

    assert result['ok'] is True
    entry = next(item for item in result['repositories'] if item['id'] == repository_id)
    assert entry['allowed_source_roots'] == ['src']
    assert entry['allowed_test_commands'] == ['pytest -q']
    # 把绝对路径交给 Agent，配了文件系统工具的 Agent 就能绕开 allowed_source_roots
    assert 'local_path' not in entry
    assert str(tmp_path) not in json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_missing_repository_is_reported_not_raised(monkeypatch, tmp_path):
    await _setup(monkeypatch, tmp_path)

    for tool, kwargs in (
        (mcp_server.search_code, {'query': 'validate'}),
        (mcp_server.read_code, {'path': 'src/service.py'}),
        (mcp_server.list_allowed_test_commands, {}),
        (mcp_server.run_allowed_test, {'task_id': 1, 'command': 'pytest -q'}),
    ):
        result = await tool(999, **kwargs)
        assert result['ok'] is False and result['error'] == 'boundary_rejected'


@pytest.mark.asyncio
async def test_search_and_read_stay_inside_allowed_source_roots(monkeypatch, tmp_path):
    repository_id, _ = await _setup(monkeypatch, tmp_path)

    found = await mcp_server.search_code(repository_id, query='validate')
    assert found['ok'] is True and found['count'] >= 1
    assert {item['path'] for item in found['results']} == {'src/service.py'}

    read = await mcp_server.read_code(repository_id, 'src/service.py', symbol='validate')
    assert read['ok'] is True and 'return value > 0' in read['content']

    escaped = await mcp_server.read_code(repository_id, 'outside.py')
    assert escaped['ok'] is False and escaped['error'] == 'boundary_rejected'


@pytest.mark.asyncio
async def test_search_rejects_out_of_range_arguments_instead_of_clamping(monkeypatch, tmp_path):
    repository_id, _ = await _setup(monkeypatch, tmp_path)

    result = await mcp_server.search_code(repository_id, query='validate', max_results=500)
    assert result['ok'] is False and result['error'] == 'boundary_rejected'


@pytest.mark.asyncio
async def test_run_allowed_test_rejects_command_outside_whitelist(monkeypatch, tmp_path):
    repository_id, _ = await _setup(monkeypatch, tmp_path)

    result = await mcp_server.run_allowed_test(repository_id, 1, 'rm -rf /')

    assert result['ok'] is False and result['error'] == 'boundary_rejected'
    assert result['allowed_test_commands'] == ['pytest -q']


@pytest.mark.asyncio
async def test_run_allowed_test_refuses_when_worktree_is_absent(monkeypatch, tmp_path):
    """补丁还没应用时必须拒绝，不能降级成"在仓库根目录跑一下"。"""
    repository_id, _ = await _setup(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(mcp_server, 'run_allowlisted', lambda *args, **kwargs: calls.append(args))

    result = await mcp_server.run_allowed_test(repository_id, 7, 'pytest -q')

    assert result['ok'] is False and 'Worktree' in result['message']
    assert calls == []


@pytest.mark.asyncio
async def test_run_allowed_test_executes_inside_the_task_worktree(monkeypatch, tmp_path):
    repository_id, _ = await _setup(monkeypatch, tmp_path)
    worktree = tmp_path / '.codeforge' / 'worktrees' / 'task-7'
    worktree.mkdir(parents=True)

    recorded = []

    def _record(command, cwd, allowed_commands=None, timeout_seconds=300):
        recorded.append((command, cwd))
        return {'status': 'passed', 'exit_code': 0, 'output': '', 'timed_out': False,
                'environment_error': None, 'duration_ms': 1}

    monkeypatch.setattr(mcp_server, 'run_allowlisted', _record)

    result = await mcp_server.run_allowed_test(repository_id, 7, 'pytest -q')

    assert result['ok'] is True and result['status'] == 'passed'
    assert recorded == [('pytest -q', worktree)]
    assert result['worktree'] == '.codeforge/worktrees/task-7'


@pytest.mark.asyncio
async def test_server_registers_all_tools_over_the_mcp_protocol(monkeypatch, tmp_path):
    """不走 stdio，用内存客户端把服务真跑一遍：工具确实注册了，且能按名字调用。"""
    from fastmcp import Client

    repository_id, _ = await _setup(monkeypatch, tmp_path)
    server = mcp_server.build_server()

    async with Client(server) as client:
        names = {tool.name for tool in await client.list_tools()}
        assert names == {'list_repositories', 'search_code', 'read_code',
                         'list_allowed_test_commands', 'run_allowed_test'}
        response = await client.call_tool('list_repositories', {})
        assert response.data['ok'] is True
        assert any(item['id'] == repository_id for item in response.data['repositories'])
