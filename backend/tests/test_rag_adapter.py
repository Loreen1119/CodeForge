import pytest

from rag_adapter import search_with_fallback


class Provider:
    def search(self, **kwargs):
        return [{"file": "src/app.py", "line": 3, "content": "return value", "symbol": "validate"}]


def test_adapter_rejects_provider_object(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises(ValueError, match="provider integration is disabled"):
        search_with_fallback(str(tmp_path), "value", provider=Provider(), allowed_source_roots=["src"])


def test_adapter_uses_local_keyword_search(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("def validate():\n    return True\n", encoding="utf-8")
    result = search_with_fallback(str(tmp_path), "return", allowed_source_roots=["src"])
    assert result[0]["path"] == "src/app.py"
    assert result[0]["match_type"] == "keyword"


def test_adapter_returns_local_result_for_malformed_provider_case(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("return True\n", encoding="utf-8")
    result = search_with_fallback(str(tmp_path), "return", allowed_source_roots=["src"])
    assert result[0]["path"] == "src/app.py"


def test_adapter_keeps_local_search_inside_source_root(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("return True\n", encoding="utf-8")
    result = search_with_fallback(str(tmp_path), "return", allowed_source_roots=["src"])
    assert result[0]["path"] == "src/app.py"

def test_adapter_reports_local_hit_without_provider(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("user_input = input()\n", encoding="utf-8")
    result = search_with_fallback(str(tmp_path), "input", allowed_source_roots=["src"])
    assert result.status == "hit"
    assert result[0]["path"] == "src/app.py"

def test_provider_callable_is_rejected_before_local_search(tmp_path):
    (tmp_path / "src").mkdir()
    calls = []
    def provider(**kwargs):
        calls.append(kwargs["query"])
        return [{"path":"src/app.py","line":1,"content":"input"}]
    with pytest.raises(ValueError, match="provider integration is disabled"):
        search_with_fallback(str(tmp_path), "input", provider=provider, allowed_source_roots=["src"])
    assert calls == []
