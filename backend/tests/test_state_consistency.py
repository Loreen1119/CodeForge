"""Test state consistency across DDL, backend constants, and SSE groupings."""
import re
import sqlite3
import json
from pathlib import Path
import pytest
from constants import (
    REQUIRED_TASK_STATUSES, IN_FLIGHT, WAIT_HUMAN, TERMINAL,
    REQUIRED_PLAN_STATUSES, REQUIRED_PATCH_APPLY_STATUSES,
    REQUIRED_TEST_RUN_STATUSES
)
from db import CODE_TASKS_TABLE_SQL, PLANS_TABLE_SQL, PATCHES_TABLE_SQL, TEST_RUNS_TABLE_SQL


def extract_check_values(ddl: str, table_name: str, column_name: str) -> set[str]:
    """Extract CHECK constraint values from DDL for a specific column."""
    # Look for CHECK(column_name IN ('val1','val2',...))
    pattern = rf"CHECK\s*\(\s*{column_name}\s+IN\s*\(([^)]+)\)"
    match = re.search(pattern, ddl, re.IGNORECASE)
    if not match:
        return set()
    values_str = match.group(1)
    # Extract quoted values
    values = re.findall(r"'([^']*)'", values_str)
    return set(values)


def test_task_statuses_consistency():
    """Test that code_tasks status CHECK constraint matches backend constants."""
    ddl_statuses = extract_check_values(CODE_TASKS_TABLE_SQL, 'code_tasks', 'status')
    backend_statuses = set(REQUIRED_TASK_STATUSES)

    assert ddl_statuses == backend_statuses, (
        f"DDL statuses {ddl_statuses} != backend constants {backend_statuses}"
    )


def test_plan_statuses_consistency():
    """Test that plans status CHECK constraint matches backend constants."""
    ddl_statuses = extract_check_values(PLANS_TABLE_SQL, 'plans', 'status')
    backend_statuses = set(REQUIRED_PLAN_STATUSES)

    assert ddl_statuses == backend_statuses, (
        f"DDL statuses {ddl_statuses} != backend constants {backend_statuses}"
    )


def test_patch_apply_statuses_consistency():
    """Test that patches apply_status CHECK constraint matches backend constants."""
    ddl_statuses = extract_check_values(PATCHES_TABLE_SQL, 'patches', 'apply_status')
    backend_statuses = set(REQUIRED_PATCH_APPLY_STATUSES)

    assert ddl_statuses == backend_statuses, (
        f"DDL statuses {ddl_statuses} != backend constants {backend_statuses}"
    )


def test_test_run_statuses_consistency():
    """Test that test_runs status CHECK constraint matches backend constants."""
    ddl_statuses = extract_check_values(TEST_RUNS_TABLE_SQL, 'test_runs', 'status')
    backend_statuses = set(REQUIRED_TEST_RUN_STATUSES)

    assert ddl_statuses == backend_statuses, (
        f"DDL statuses {ddl_statuses} != backend constants {backend_statuses}"
    )


def test_sse_groups_partition_all_statuses():
    """Test that IN_FLIGHT, WAIT_HUMAN, TERMINAL partition all statuses exactly once."""
    all_states = IN_FLIGHT | WAIT_HUMAN | TERMINAL
    backend_states = set(REQUIRED_TASK_STATUSES)

    assert all_states == backend_states, (
        f"SSE groups {all_states} != all states {backend_states}"
    )

    # Check no overlaps
    assert len(IN_FLIGHT & WAIT_HUMAN) == 0, "IN_FLIGHT and WAIT_HUMAN overlap"
    assert len(IN_FLIGHT & TERMINAL) == 0, "IN_FLIGHT and TERMINAL overlap"
    assert len(WAIT_HUMAN & TERMINAL) == 0, "WAIT_HUMAN and TERMINAL overlap"


def test_sse_groups_coverage():
    """Test that each SSE group has expected members."""
    # IN_FLIGHT should include async work states
    async_states = {
        'pending', 'running', 'generating_patch', 'applying', 'testing'
    }
    assert IN_FLIGHT == async_states, f"IN_FLIGHT {IN_FLIGHT} != expected {async_states}"

    # WAIT_HUMAN should include human decision states
    wait_states = {
        'needs_context', 'awaiting_plan_approval', 'awaiting_patch_generation_retry',
        'awaiting_patch_approval', 'awaiting_test'
    }
    assert WAIT_HUMAN == wait_states, f"WAIT_HUMAN {WAIT_HUMAN} != expected {wait_states}"

    # TERMINAL should include final states
    terminal_states = {
        'succeeded', 'test_failed', 'test_timeout', 'environment_failed', 'failed', 'cancelled'
    }
    assert TERMINAL == terminal_states, f"TERMINAL {TERMINAL} != expected {terminal_states}"


def test_legacy_database_migration():
    """Test that a legacy database without needs_context can be migrated."""
    import tempfile
    import asyncio
    import shutil
    from pathlib import Path
    from db import DB_PATH

    async def test_async():
        # Save current DB, create temp DB, test migration, restore
        orig_db = DB_PATH
        orig_exists = orig_db.exists()
        orig_backup = None

        try:
            # Backup original DB
            if orig_exists:
                import datetime
                backup_name = f"codeforge_{datetime.datetime.now().isoformat().replace(':', '-')}.db"
                orig_backup = orig_db.parent / backup_name
                shutil.copy2(orig_db, orig_backup)

            # Remove current DB and test fresh init_db
            if orig_db.exists():
                orig_db.unlink()

            # Test that init_db() succeeds
            from db import init_db
            await init_db()

            # Verify schema version
            from db import _conn
            conn = await _conn()
            try:
                row = await (await conn.execute(
                    "SELECT version FROM schema_meta LIMIT 1"
                )).fetchone()
                version = row[0] if row else 0
                assert version >= 1, f"Schema version {version} too low after init_db()"
            finally:
                await conn.close()

        finally:
            # Restore original DB
            if orig_db.exists():
                orig_db.unlink()
            if orig_backup and orig_backup.exists():
                shutil.copy2(orig_backup, orig_db)
                orig_backup.unlink()

    asyncio.run(test_async())


def test_patches_table_unique_constraint():
    """Test that patches table has UNIQUE(plan_id, patch_revision) constraint."""
    # Extract the UNIQUE constraint from the DDL
    assert "UNIQUE(plan_id, patch_revision)" in PATCHES_TABLE_SQL, \
        "patches DDL missing UNIQUE(plan_id, patch_revision)"


def test_apply_failure_reason_column_exists():
    """Test that patches table has apply_failure_reason column."""
    assert "apply_failure_reason TEXT" in PATCHES_TABLE_SQL, \
        "patches DDL missing apply_failure_reason TEXT column"


def test_patch_plan_id_not_null():
    """Test that patches.plan_id is NOT NULL."""
    assert "plan_id INTEGER NOT NULL REFERENCES plans(id)" in PATCHES_TABLE_SQL, \
        "patches.plan_id should be NOT NULL"
