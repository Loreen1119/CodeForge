"""数据层：任务模板表 + code_tasks 模板关联列的建表、迁移与幂等性。

这几条用例锁的是"模板能存在 SQLite 里"这个前提：新库要能建出表，
老库要能就地补列（不是靠删库重建），重复启动不能炸。
"""
import sqlite3

import pytest

import db

_TEMPLATE_COLUMNS = {
    "id", "name", "description", "repository_id", "issue_pattern",
    "params_json", "source_task_id", "use_count", "last_used_at",
    "created_at", "updated_at",
}


def _columns(path, table):
    con = sqlite3.connect(str(path))
    try:
        return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
    finally:
        con.close()


async def _init(monkeypatch, tmp_path, name="schema.db"):
    db_path = tmp_path / name
    monkeypatch.setattr(db, "DB_PATH", db_path)
    await db.init_db()
    return db_path


@pytest.mark.asyncio
async def test_fresh_init_creates_template_schema(monkeypatch, tmp_path):
    """全新库：task_templates 建出来，code_tasks 带上两个新列。"""
    db_path = await _init(monkeypatch, tmp_path)

    con = sqlite3.connect(str(db_path))
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()

    assert "task_templates" in tables
    assert _TEMPLATE_COLUMNS <= _columns(db_path, "task_templates")
    assert {"template_id", "template_params_json"} <= _columns(db_path, "code_tasks")


@pytest.mark.asyncio
async def test_legacy_db_gains_template_columns_without_losing_rows(monkeypatch, tmp_path):
    """老库（v3、code_tasks 无模板列）：就地补列，历史行保留且新列为 NULL。"""
    db_path = tmp_path / "legacy.db"
    con = sqlite3.connect(str(db_path))
    try:
        con.executescript(
            """
CREATE TABLE code_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_id INTEGER NOT NULL,
    issue_title TEXT NOT NULL,
    issue_body TEXT NOT NULL,
    status TEXT NOT NULL,
    current_agent TEXT,
    attempt_number INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    plan_decision TEXT,
    reviewer_feedback TEXT,
    next_event_seq INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TEXT,
    finished_at TEXT
);
INSERT INTO code_tasks(repository_id, issue_title, issue_body, status)
VALUES (1, '旧任务', '迁移前就已存在', 'running');
CREATE TABLE schema_meta (version INTEGER PRIMARY KEY DEFAULT 1);
INSERT INTO schema_meta(version) VALUES(3);
"""
        )
        con.commit()
    finally:
        con.close()

    monkeypatch.setattr(db, "DB_PATH", db_path)
    await db.init_db()

    assert {"template_id", "template_params_json"} <= _columns(db_path, "code_tasks")

    con = sqlite3.connect(str(db_path))
    try:
        rows = list(con.execute("SELECT issue_title, status, template_id, template_params_json FROM code_tasks"))
        version = con.execute("SELECT version FROM schema_meta").fetchone()[0]
    finally:
        con.close()

    assert rows == [("旧任务", "running", None, None)]
    assert version == len(db.MIGRATIONS)


@pytest.mark.asyncio
async def test_init_db_is_idempotent(monkeypatch, tmp_path):
    """连续启动两次：第二次既不报错，也不重复建表/重复加列。"""
    db_path = await _init(monkeypatch, tmp_path, name="twice.db")
    before = _columns(db_path, "code_tasks")
    await db.init_db()
    assert _columns(db_path, "code_tasks") == before
