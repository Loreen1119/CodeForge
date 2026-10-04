import subprocess

import httpx
import pytest

import db
import rag_adapter
import task_service
from main import api


@pytest.mark.asyncio
async def test_search_and_read_api_are_repository_bounded(monkeypatch, tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "def validate(value):\n    return value > 0\n", encoding="utf-8"
    )
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/repositories",
            json={
                "name": "sample",
                "local_path": str(tmp_path),
                "allowed_source_roots": ["src"],
            },
        )
        assert created.status_code == 201
        repository_id = created.json()["id"]

        searched = await client.post(
            f"/api/repositories/{repository_id}/search",
            json={"query": "return"},
        )
        assert searched.status_code == 200
        assert searched.json()[0]["path"] == "src/service.py"
        assert searched.json()[0]["symbol"] == "validate"

        read = await client.post(
            f"/api/repositories/{repository_id}/read",
            json={"path": "src/service.py", "symbol": "validate"},
        )
        assert read.status_code == 200
        assert "return value > 0" in read.json()["content"]

        escaped = await client.post(
            f"/api/repositories/{repository_id}/read",
            json={"path": "../backend/main.py"},
        )
        assert escaped.status_code == 400


@pytest.mark.asyncio
async def test_search_api_does_not_depend_on_rag_provider(monkeypatch, tmp_path):
    """公开检索接口必须直连本地检索，不能经过 RAG 适配器。

    适配器在没有 provider 时返回空结果，阶段 B 曾因此让这个接口永远返回 []。
    既有用例只在"没有 provider"时能抓到，一旦将来全局注入 provider 就会失效，
    所以这里直接禁止调用适配器，并断言 main 不再持有它的引用。
    """
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "service.py").write_text(
        "def validate(value):\n    return value > 0\n", encoding="utf-8"
    )
    db_path = tmp_path / "api.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(task_service, "DB_PATH", db_path)
    await db.init_db()

    def _adapter_must_not_be_used(*args, **kwargs):
        raise AssertionError("公开检索接口不应经过 RAG 适配器")

    monkeypatch.setattr(rag_adapter, "search_with_fallback", _adapter_must_not_be_used)

    transport = httpx.ASGITransport(app=api)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/repositories",
            json={"name": "sample", "local_path": str(tmp_path), "allowed_source_roots": ["src"]},
        )
        assert created.status_code == 201
        repository_id = created.json()["id"]

        searched = await client.post(
            f"/api/repositories/{repository_id}/search", json={"query": "return"}
        )
        assert searched.status_code == 200
        assert [item["path"] for item in searched.json()] == ["src/service.py"]

    assert not hasattr(api, "search_with_fallback")
    import main
    assert not hasattr(main, "search_with_fallback"), "main 不应再导入 RAG 适配器"
