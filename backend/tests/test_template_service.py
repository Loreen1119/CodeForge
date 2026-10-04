"""任务模板服务：存取往返、参数完整落库、边界 fail-closed。

这一层锁两件事：
1. 模板存进 SQLite 后读回来与写入时逐项一致（存的是完整参数，不是空对象）；
2. 模板只能收窄安全边界 —— 越界参数在契约层被拒，越白名单的测试命令在收敛层被拒。
"""
import json
import sqlite3

import pytest
from pydantic import ValidationError

import db
import template_service
from schemas import TemplateParams

_ERROR = template_service.TemplateBoundaryError


async def _setup(monkeypatch, tmp_path):
    db_path = tmp_path / "templates.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    await db.init_db()
    return db_path


def _add_repository(db_path, *, commands=("pytest -q",), name="sample") -> int:
    connection = sqlite3.connect(str(db_path))
    try:
        cursor = connection.execute(
            "INSERT INTO repositories(name,local_path,default_branch,allowed_source_roots,allowed_test_commands)"
            " VALUES(?,?,?,?,?)",
            (name, f"C:/tmp/{name}", "main", json.dumps(["src"]), json.dumps(list(commands))),
        )
        connection.commit()
        return cursor.lastrowid
    finally:
        connection.close()


def _repository(db_path, repository_id: int) -> dict:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        return dict(connection.execute("SELECT * FROM repositories WHERE id=?", (repository_id,)).fetchone())
    finally:
        connection.close()


def _stored_params(db_path, template_id: int) -> dict:
    connection = sqlite3.connect(str(db_path))
    try:
        return json.loads(connection.execute(
            "SELECT params_json FROM task_templates WHERE id=?", (template_id,)
        ).fetchone()[0])
    finally:
        connection.close()


@pytest.mark.asyncio
async def test_default_template_round_trip_stores_full_params(monkeypatch, tmp_path):
    """空模板落库的是完整参数表，读回来等于空参数表本身。"""
    db_path = await _setup(monkeypatch, tmp_path)
    assert db_path == db.DB_PATH  # 防呆：确认写的是临时库，不是 backend/codeforge.db

    template_id = await template_service.create_template(name="标准链路")
    template = await template_service.get_template(template_id)

    assert template is not None
    assert template["name"] == "标准链路"
    assert template["params"] == TemplateParams()
    assert template["use_count"] == 0 and template["last_used_at"] is None
    # 落库列里必须涵盖全部参数键，读回来才不依赖代码默认值补齐
    assert set(_stored_params(db_path, template_id)) == set(TemplateParams.model_fields)

    await template_service.mark_template_used(template_id)
    used = await template_service.get_template(template_id)
    assert used["use_count"] == 1 and used["last_used_at"] is not None


@pytest.mark.asyncio
async def test_list_and_delete(monkeypatch, tmp_path):
    await _setup(monkeypatch, tmp_path)

    first = await template_service.create_template(name="模板A")
    second = await template_service.create_template(name="模板B")

    listed = await template_service.list_templates()
    assert [item["id"] for item in listed] == [second, first]  # 最新在前

    assert await template_service.delete_template(first) is True
    assert await template_service.get_template(first) is None
    assert await template_service.delete_template(first) is False

    # name 有唯一约束，重名必须被库里挡住（不是靠应用层判断）
    with pytest.raises(sqlite3.IntegrityError):
        await template_service.create_template(name="模板B")


def test_boundary_params_are_rejected_not_clamped():
    """越界参数必须在契约层被拒，不能静默截断成合法值。"""
    for bad in (
        {"max_changed_lines": 80},        # 硬上限 50
        {"test_timeout_seconds": 999},    # 硬上限 300
        {"max_proposed_changes": 6},      # 硬上限 5
        {"patch_provider": "gpt"},        # 未知 provider
        {"max_changed_line": 20},         # 拼错的键名
    ):
        with pytest.raises(ValidationError):
            TemplateParams(**bad)


@pytest.mark.asyncio
async def test_test_command_must_hit_repository_whitelist(monkeypatch, tmp_path):
    """模板里的测试命令只能取自仓库白名单：库内放行，越界拒绝。"""
    db_path = await _setup(monkeypatch, tmp_path)
    repository_id = _add_repository(db_path, commands=("pytest -q",))
    repository = _repository(db_path, repository_id)

    inside = await template_service.create_template(name="白名单内", params=TemplateParams(test_command="pytest -q"))
    resolved = template_service.resolve_params(
        await template_service.get_template(inside), repository, repository_id=repository_id
    )
    assert resolved["test_command"] == "pytest -q"
    assert set(resolved) == set(TemplateParams.model_fields)

    outside = await template_service.create_template(name="白名单外", params=TemplateParams(test_command="rm -rf /"))
    with pytest.raises(_ERROR, match="不在目标仓库白名单中"):
        template_service.resolve_params(
            await template_service.get_template(outside), repository, repository_id=repository_id
        )


@pytest.mark.asyncio
async def test_repository_bound_template_rejects_other_repository(monkeypatch, tmp_path):
    """绑定了仓库的模板不能用在别的仓库；repository_id 为空的模板全局可用。"""
    db_path = await _setup(monkeypatch, tmp_path)
    repository_id = _add_repository(db_path, name="first")
    other_id = _add_repository(db_path, name="second")

    bound = await template_service.create_template(name="只给 first", repository_id=repository_id)
    template = await template_service.get_template(bound)
    repository = _repository(db_path, repository_id)

    assert template_service.resolve_params(template, repository, repository_id=repository_id)['test_command'] is None
    with pytest.raises(_ERROR, match="不能用于仓库"):
        template_service.resolve_params(template, _repository(db_path, other_id), repository_id=other_id)


def test_unknown_param_keys_are_ignored_on_read():
    """模板落库后参数表若演进，多出来的键不该让整条记录读不出来。"""
    parsed = template_service.parse_params({"max_changed_lines": 20, "未来才有的键": 1})
    assert parsed.max_changed_lines == 20
