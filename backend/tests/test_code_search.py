import pytest

from code_search import read_code, search_repository


def test_search_returns_path_line_symbol_and_context(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n", encoding="utf-8")
    results = search_repository(str(tmp_path), "return", allowed_source_roots=["src"])
    assert results[0]["path"] == "src/app.py"
    assert results[0]["matched_line"] == 2
    assert results[0]["symbol"] == "validate"
    assert "validate" in results[0]["snippet"]


def test_search_excludes_build_and_unallowed_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "dist").mkdir()
    (tmp_path / "src" / "good.ts").write_text("export function target() {}\n", encoding="utf-8")
    (tmp_path / "dist" / "bad.ts").write_text("target\n", encoding="utf-8")
    results = search_repository(str(tmp_path), "target", allowed_source_roots=["src"])
    assert [item["path"] for item in results] == ["src/good.ts"]


def test_symbol_search(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text("function buildThing() {\n  return true\n}\n", encoding="utf-8")
    results = search_repository(str(tmp_path), "", symbol="buildThing", allowed_source_roots=["src"])
    assert results[0]["match_type"] == "symbol"


def test_search_ranks_symbols_before_keywords_and_supports_exported_declarations(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export function target() {}\n", encoding="utf-8")
    (tmp_path / "src" / "b.ts").write_text("const note = 'target'\n", encoding="utf-8")
    results = search_repository(str(tmp_path), "target", symbol="target", allowed_source_roots=["src"])
    assert [(item["path"], item["match_type"]) for item in results] == [("src/a.ts", "symbol"), ("src/b.ts", "keyword")]


def test_filename_search_returns_one_result_per_matching_file_in_path_order(tmp_path):
    (tmp_path / "src" / "z").mkdir(parents=True)
    (tmp_path / "src" / "a").mkdir()
    (tmp_path / "src" / "z" / "handler.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "src" / "a" / "handler.py").write_text("pass\n", encoding="utf-8")
    results = search_repository(str(tmp_path), "", filename="handler", allowed_source_roots=["src"])
    assert [item["path"] for item in results] == ["src/a/handler.py", "src/z/handler.py"]
    assert all(item["match_type"] == "filename" for item in results)


def test_keyword_result_reports_enclosing_symbol(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate(value):\n    return value > 0\n", encoding="utf-8")
    result = search_repository(str(tmp_path), "return", allowed_source_roots=["src"])[0]
    assert result["symbol"] == "validate"


def test_read_code_by_symbol_and_rejects_path_escape(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def first():\n    return 1\n\ndef second():\n    return 2\n", encoding="utf-8")
    result = read_code(str(tmp_path), "src/app.py", symbol="second", allowed_source_roots=["src"])
    assert result["line_start"] == 4
    assert result["line_end"] == 5
    assert "return 2" in result["content"]
    with pytest.raises(ValueError):
        read_code(str(tmp_path), "../app.py", allowed_source_roots=["src"])


def test_read_code_honors_line_range_and_allowed_roots(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.ts").write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = read_code(str(tmp_path), "src/app.ts", start_line=2, end_line=2, allowed_source_roots=["src"])
    assert result["content"] == "two"
    with pytest.raises(ValueError):
        read_code(str(tmp_path), "src/app.ts", allowed_source_roots=["other"])


def test_search_and_read_fail_closed_without_allowlist(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("return True\n", encoding="utf-8")
    with pytest.raises(ValueError):
        search_repository(str(tmp_path), "return")
    with pytest.raises(ValueError):
        read_code(str(tmp_path), "src/app.py")
