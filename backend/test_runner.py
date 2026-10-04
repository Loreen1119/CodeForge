"""Allowlisted syntax and test execution inside an isolated worktree."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path


def syntax_check(worktree: Path, changed_files: list[str]) -> tuple[bool, str]:
    for relative in changed_files:
        path = worktree / relative
        if path.suffix == ".py":
            result = subprocess.run(["python", "-m", "py_compile", str(path)], cwd=worktree, capture_output=True, text=True)
            if result.returncode:
                return False, result.stderr or result.stdout
    return True, ""


def run_allowlisted(command: str, worktree: Path, allowed_commands: list[str] | None = None, timeout_seconds: int = 300) -> dict:
    if allowed_commands is None or command not in allowed_commands:
        return {"status": "environment_error", "exit_code": None, "output": "命令不在白名单中", "timed_out": False, "environment_error": "命令不在白名单中", "duration_ms": 0}
    started = time.monotonic()
    try:
        result = subprocess.run(command, cwd=worktree, shell=True, capture_output=True, text=True, timeout=min(timeout_seconds, 300))
        return {"status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "output": (result.stdout + result.stderr)[-20000:], "timed_out": False, "environment_error": None, "duration_ms": int((time.monotonic() - started) * 1000)}
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or ""))[-20000:]
        return {"status": "timed_out", "exit_code": None, "output": output, "timed_out": True, "environment_error": None, "duration_ms": int((time.monotonic() - started) * 1000)}
    except (OSError, ValueError) as exc:
        return {"status": "environment_error", "exit_code": None, "output": str(exc), "timed_out": False, "environment_error": str(exc), "duration_ms": int((time.monotonic() - started) * 1000)}
