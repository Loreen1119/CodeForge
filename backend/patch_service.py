"""Safety checks for proposed unified diffs; this module never writes files."""
from __future__ import annotations

import re
from pathlib import Path
from pydantic import BaseModel, Field


class PatchCheck(BaseModel):
    valid: bool
    changed_files: list[str] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    changed_lines: int = 0
    risk_level: str
    findings: list[str] = Field(default_factory=list)


_OLD_FILE_RE = re.compile(r"^--- (?:a/)?(.+)$")
_NEW_FILE_RE = re.compile(r"^\+\+\+ (?:b/)?(.+)$")
_HUNK_RE = re.compile(r"^@@ .+ @@")
_DANGEROUS = ("rm -rf", "git reset --hard", "git clean -fd", "os.remove(", "shutil.rmtree(")


def _diff_path(value: str) -> str:
    """Extract the path from a unified-diff header, ignoring optional timestamps."""
    return value.split("\t", 1)[0].strip()


def validate_patch(diff: str, repository_root: str, allowed_source_roots: list[str] | None = None, max_changed_lines: int = 50) -> PatchCheck:
    findings: list[str] = []
    files: list[str] = []
    old_files: list[str] = []
    additions = deletions = 0
    if not diff.strip():
        return PatchCheck(valid=False, risk_level="high", findings=["Diff 不能为空"])
    root = Path(repository_root).resolve()
    allowed = [(root / item).resolve() for item in (allowed_source_roots or [])]
    if not allowed:
        return PatchCheck(valid=False, risk_level="high", findings=["必须显式配置至少一个允许源码目录"])
    if any(item == root or not (item == root or root in item.parents) or not item.is_dir() for item in allowed):
        return PatchCheck(valid=False, risk_level="high", findings=["允许源码目录无效"])
    lines = diff.splitlines()
    if not any(_HUNK_RE.match(line) for line in lines):
        findings.append("不是有效的 unified diff")
    for line in lines:
        old_match = _OLD_FILE_RE.match(line)
        new_match = _NEW_FILE_RE.match(line)
        if old_match:
            old_path = _diff_path(old_match.group(1))
            if old_path != "/dev/null":
                old_files.append(old_path)
                old_candidate = (root / old_path).resolve()
                try:
                    old_candidate.relative_to(root)
                except ValueError:
                    findings.append(f"文件路径超出仓库范围: {old_path}")
                if allowed and not any(old_candidate == base or base in old_candidate.parents for base in allowed):
                    findings.append(f"文件不在允许源码目录: {old_path}")
        match = new_match
        if match:
            path_text = _diff_path(match.group(1))
            if path_text == "/dev/null":
                if old_files and not files:
                    files.append(old_files[-1])
                continue
            candidate = (root / path_text).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                findings.append(f"文件路径超出仓库范围: {path_text}")
                continue
            if allowed and not any(candidate == base or base in candidate.parents for base in allowed):
                findings.append(f"文件不在允许源码目录: {path_text}")
            if Path(path_text).suffix.casefold() not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                findings.append(f"文件类型不受支持: {path_text}")
            if path_text not in files:
                files.append(path_text)
        elif line.startswith("+") and not line.startswith("+++"):
            additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
        if any(token in line.casefold() for token in _DANGEROUS):
            findings.append("Diff 包含潜在危险操作")
    changed_lines = additions + deletions
    if old_files and old_files != files:
        findings.append("Diff 的 --- 与 +++ 文件路径不一致")
    if len(files) != 1:
        findings.append("首版 Patch 只能修改单个文件")
    if changed_lines > max_changed_lines:
        findings.append(f"变更行数超过 {max_changed_lines} 行限制")
    risk = "high" if findings else ("medium" if changed_lines > 20 else "low")
    return PatchCheck(valid=not findings, changed_files=files, additions=additions, deletions=deletions, changed_lines=changed_lines, risk_level=risk, findings=list(dict.fromkeys(findings)))
