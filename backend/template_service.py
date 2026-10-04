"""任务模板的持久化与参数收敛。

模板 = 五步链路（需求分析 → 代码检索 → 方案生成 → Patch 生成 → 测试审查）的可调参数包。
本模块只管两件事：

1. 存取：`task_templates` 表的增删查；
2. 收敛：把模板参数与目标仓库的安全边界求交，产出可以落库的参数快照。

边界规则是 **fail-closed**：模板只能把边界收得更紧，不能放宽。
越界一律抛 `TemplateBoundaryError`，不静默丢弃 —— 静默丢弃会让模板"看起来生效，
实际没生效"，比直接报错更难查。这与 `allowed_source_roots` 为空即拒绝的既有约定一致。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping

import aiosqlite

import db
from schemas import TemplateParams

# 读取模板时按已知字段过滤：模板落库后如果参数表发生演进，多出来的键不该让整条记录读不出来。
# 写入侧仍然靠 TemplateParams 的 extra='forbid' 拦住拼错的参数名。
_KNOWN_PARAM_FIELDS = set(TemplateParams.model_fields)


class TemplateBoundaryError(ValueError):
    """模板参数越过目标仓库的安全边界。"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _conn():
    # 刻意走 db.DB_PATH 这个模块属性，而不是 from db import DB_PATH：
    # 全仓只留一个 DB 路径来源，测试里 monkeypatch db.DB_PATH 就够，
    # 不会因为漏改某个模块的副本而把测试数据写进真实库。
    connection = await aiosqlite.connect(str(db.DB_PATH))
    connection.row_factory = aiosqlite.Row
    return connection


def parse_params(raw: Any) -> TemplateParams:
    """把库里的 params_json 还原成 TemplateParams（未知键忽略，缺失键取默认值）。"""
    data = raw if isinstance(raw, dict) else json.loads(raw or '{}')
    return TemplateParams.model_validate({key: value for key, value in data.items() if key in _KNOWN_PARAM_FIELDS})


def _row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    data = dict(row)
    data['params'] = parse_params(data.pop('params_json'))
    return data


async def create_template(*, name: str, params: TemplateParams | None = None, description: str = '',
                          repository_id: int | None = None, issue_pattern: str = '',
                          source_task_id: int | None = None) -> int:
    """落一条模板。params 缺省时存的是 TemplateParams 的完整默认值（不是空对象），
    这样读回来的模板本身就是自描述的，不依赖代码里的默认值补齐。"""
    resolved = params or TemplateParams()
    connection = await _conn()
    try:
        cursor = await connection.execute(
            "INSERT INTO task_templates(name,description,repository_id,issue_pattern,params_json,source_task_id)"
            " VALUES(?,?,?,?,?,?)",
            (name, description, repository_id, issue_pattern,
             json.dumps(resolved.model_dump(), ensure_ascii=False), source_task_id),
        )
        await connection.commit()
        return cursor.lastrowid
    finally:
        await connection.close()


async def list_templates() -> list[dict[str, Any]]:
    connection = await _conn()
    try:
        rows = await (await connection.execute("SELECT * FROM task_templates ORDER BY id DESC")).fetchall()
        return [_row_to_dict(row) for row in rows]
    finally:
        await connection.close()


async def get_template(template_id: int) -> dict[str, Any] | None:
    connection = await _conn()
    try:
        row = await (await connection.execute("SELECT * FROM task_templates WHERE id=?", (template_id,))).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        await connection.close()


async def delete_template(template_id: int) -> bool:
    connection = await _conn()
    try:
        cursor = await connection.execute("DELETE FROM task_templates WHERE id=?", (template_id,))
        await connection.commit()
        return cursor.rowcount == 1
    finally:
        await connection.close()


async def mark_template_used(template_id: int) -> None:
    """记录一次使用。只动 use_count / last_used_at，不动 updated_at ——
    调用不算"模板定义被改过"。"""
    connection = await _conn()
    try:
        await connection.execute(
            "UPDATE task_templates SET use_count=use_count+1, last_used_at=? WHERE id=?",
            (_now(), template_id),
        )
        await connection.commit()
    finally:
        await connection.close()


def resolve_params(template: Mapping[str, Any], repository: Mapping[str, Any], *,
                   repository_id: int) -> dict[str, Any]:
    """模板参数 × 目标仓库边界 → 可落库的参数快照。

    两条收窄规则：
    - 模板的 `test_command` 必须精确命中目标仓库白名单，否则拒绝（不静默丢弃）；
    - 模板若绑定了仓库（`repository_id` 非空），只能用于该仓库；为空表示全局可复用。

    检索范围不由模板决定，永远取自仓库的 `allowed_source_roots`，所以这里不需要处理。
    """
    params: TemplateParams = template['params']
    bound_repository_id = template.get('repository_id')
    if bound_repository_id is not None and int(bound_repository_id) != int(repository_id):
        raise TemplateBoundaryError(
            f"模板绑定的是仓库 {bound_repository_id}，不能用于仓库 {repository_id}"
        )
    allowed_commands = json.loads(repository['allowed_test_commands'] or '[]')
    if params.test_command and params.test_command not in allowed_commands:
        raise TemplateBoundaryError(f"模板的测试命令不在目标仓库白名单中: {params.test_command}")
    return params.model_dump()
