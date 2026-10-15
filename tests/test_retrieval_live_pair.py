"""真实向量诊断的复制范围、调用预算和异常脱敏契约。"""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import httpx
import pytest

from scripts.eval import retrieval_live_pair as live


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def prepare(monkeypatch, tmp_path):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    source = tmp_path / "data/qdrant"
    write(source / "meta.json", {"collections": {"globex_products": {"vectors": {"size": 1024, "distance": "Cosine"}}}, "aliases": {}})
    write(source / "collection/globex_products/storage.sqlite", {"public_catalog_only": True})
    write(source / ".lock", "source-lock")
    write(source / "collection/private_collection/storage.sqlite", "must-not-copy")
    write(tmp_path / "data/globex.db", "private-buyer-db")
    write(tmp_path / ".env", "must-not-copy")
    write(tmp_path / "data/catalog-v3.jsonl", {"product_id": "P1"})
    settings_module = __import__("app.infrastructure.settings", fromlist=["load_settings"])
    real_settings = settings_module.load_settings(require_llm_key=False)
    monkeypatch.setattr(settings_module, "load_settings", lambda **_: replace(real_settings, data_dir=tmp_path / "data"))
    return source


def test_failure_evidence_contains_only_safe_status_code():
    request = httpx.Request("POST", "https://private.invalid/?api_key=secret")
    response = httpx.Response(403, json={"error": {"code": "Quota.Exceeded", "message": "secret buyer text"}}, request=request)
    error = httpx.HTTPStatusError("secret buyer text", request=request, response=response)
    assert live.safe_failure(error) == {"error_type": "HTTPStatusError", "http_status": 403, "code": "Quota.Exceeded"}
    response = httpx.Response(403, json={"error": {"code": "secret buyer text"}}, request=request)
    assert "code" not in live.safe_failure(httpx.HTTPStatusError("secret", request=request, response=response))


async def test_inspection_copies_only_selected_collection_and_keeps_original(monkeypatch, tmp_path):
    source = prepare(monkeypatch, tmp_path)
    import app.infrastructure.persistence.seed_products as seeds
    import app.infrastructure.vector.qdrant_product_index as indexes
    monkeypatch.setattr(seeds, "_product_from_record", lambda record: SimpleNamespace(product_id=record["product_id"]))

    class Index:
        embedding_key = "identity"
        def __init__(self, settings):
            assert settings.qdrant_url == ""
            self._client = self
        async def products_needing_embeddings(self, products): return []
        async def count(self, name, exact): return SimpleNamespace(count=1)
        async def close(self): pass

    monkeypatch.setattr(indexes, "QdrantProductIndex", Index)
    evidence = tmp_path / "eval/verification/run"
    report = await live.inspect_index(evidence)
    assert report["status"] == "complete"
    assert report["original_index_unchanged"]
    assert report["external_api_calls"] == 0
    copied = evidence / "isolated-live-index/qdrant"
    assert set(live.fingerprint(copied)) == {"meta.json", "collection/globex_products/storage.sqlite"}
    assert live.fingerprint(source) == report["original_index_before"]


async def test_inspection_rejects_extra_collection_metadata(monkeypatch, tmp_path):
    source = prepare(monkeypatch, tmp_path)
    meta = json.loads((source / "meta.json").read_text())
    meta["collections"]["private"] = {}
    write(source / "meta.json", meta)
    evidence = tmp_path / "eval/verification/run"
    report = await live.inspect_index(evidence)
    assert report["status"] == "failed"
    assert report["original_index_unchanged"]
    assert not (evidence / "isolated-live-index").exists()


async def test_incomplete_index_allows_one_probe_and_cannot_repeat_same_budget(monkeypatch, tmp_path):
    prepare(monkeypatch, tmp_path)
    import app.infrastructure.embedding.openai_embedding_client as embeddings
    import app.infrastructure.vector.embedding_identity as identity
    calls = []

    class Embedder:
        def __init__(self, settings, timeout_seconds): assert timeout_seconds == 20
        async def embed(self, text):
            calls.append(text)
            return [.1, .2]

    monkeypatch.setattr(embeddings, "OpenAIEmbeddingClient", Embedder)
    monkeypatch.setattr(identity, "embedding_identity", lambda _: "identity")
    dataset = tmp_path / "eval/v2/product_retrieval.jsonl"
    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text("\n".join(json.dumps({"id": str(index), "query": "public query"}) for index in range(3)), encoding="utf-8")
    evidence = tmp_path / "eval/verification/run"
    write(evidence / "live-index-inspection.json", {"status": "incomplete", "original_index_unchanged": True,
          "embedding_identity": "identity", "catalog_sha256": live.digest(live.catalog_path()), "dimension": 2})
    report = await live.collect_vectors(evidence)
    assert len(calls) == report["external_api_calls"] == 1
    assert report["status"] == "probe_only"
    assert report["original_index_unchanged"]
    assert report["call_cost"] is None
    with pytest.raises(FileExistsError):
        await live.collect_vectors(evidence)
    assert len(calls) == 1
