import pytest

from patch_service import validate_patch


def diff(path="src/app.py", body="+    return True"):
    return f"--- a/{path}\n+++ b/{path}\n@@ -1,1 +1,2 @@\n def validate():\n{body}\n"


def test_validates_single_file_patch(tmp_path):
    (tmp_path / "src").mkdir()
    result = validate_patch(diff(), str(tmp_path), ["src"])
    assert result.valid is True
    assert result.changed_files == ["src/app.py"]
    assert result.changed_lines == 1


def test_rejects_multi_file_and_oversized_patch(tmp_path):
    (tmp_path / "src").mkdir()
    content = "\n".join(["+line"] * 51)
    result = validate_patch(diff("src/a.py", content) + diff("src/b.py"), str(tmp_path), ["src"])
    assert result.valid is False
    assert any("单个文件" in item for item in result.findings)
    assert any("50" in item for item in result.findings)


def test_rejects_escape_and_dangerous_operation(tmp_path):
    (tmp_path / "src").mkdir()
    result = validate_patch(diff("../secret.py", "+os.remove('x')"), str(tmp_path), ["src"])
    assert result.valid is False
    assert any("超出仓库" in item for item in result.findings)
    assert any("危险" in item for item in result.findings)


def test_rejects_asymmetric_unified_diff_paths(tmp_path):
    (tmp_path / "src").mkdir()
    result = validate_patch("--- a/src/a.py\n+++ b/src/b.py\n@@ -1 +1 @@\n-old\n+new\n", str(tmp_path), ["src"])
    assert result.valid is False
    assert any("不一致" in item for item in result.findings)


def test_validates_delete_file_using_old_path(tmp_path):
    (tmp_path / "src").mkdir()
    result = validate_patch("--- a/src/app.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-remove\n", str(tmp_path), ["src"])
    assert result.changed_files == ["src/app.py"]
    assert result.valid is True


def test_accepts_standard_diff_header_timestamps(tmp_path):
    (tmp_path / "src").mkdir()
    result = validate_patch(
        "--- a/src/app.py\t2026-09-09 10:00:00 +0800\n"
        "+++ b/src/app.py\t2026-09-09 10:01:00 +0800\n"
        "@@ -1 +1 @@\n-old\n+new\n",
        str(tmp_path),
        ["src"],
    )
    assert result.valid is True
    assert result.changed_files == ["src/app.py"]


def test_patch_fails_closed_without_allowlist():
    result = validate_patch(diff(), ".")
    assert result.valid is False
    assert any("显式配置" in item for item in result.findings)


def test_patch_rejects_file_outside_configured_root(tmp_path):
    result = validate_patch(diff("tests/test_app.py"), str(tmp_path), ["src"])
    assert result.valid is False
    assert any("允许源码目录" in item for item in result.findings)
