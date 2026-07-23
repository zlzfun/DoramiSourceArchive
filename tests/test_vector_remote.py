"""RAG 服务化(v3.17)的远程模式单测。

覆盖:TEI 嵌入客户端(批量切分/响应解析)、TEI 重排客户端(index→score 映射)、
显式向量路径(save/search 把 provider 算出的向量传给 chroma add/query)、
重排 provider 的选择与优雅跳过(远程未配 rerank_url / 本地依赖缺失)。
全程 httpx.MockTransport / fake collection,不打真网、不起真服务。
"""
import asyncio
import json
import os
import sys
from dataclasses import replace

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import storage.impl.vector_storage as vs  # noqa: E402
from api.articles_view import GenericContent  # noqa: E402


# ── TEI 客户端 ────────────────────────────────────────────────────────────────

def test_tei_embedding_batches_and_parses():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload["inputs"])
        assert request.url.path == "/embed"
        assert payload["truncate"] is True
        return httpx.Response(200, json=[[0.1, 0.2] for _ in payload["inputs"]])

    client = vs.TeiEmbeddingClient("http://tei", transport=httpx.MockTransport(handler))
    texts = [f"t{i}" for i in range(vs._TEI_EMBED_BATCH + 3)]
    vectors = client.embed(texts)

    assert len(vectors) == len(texts)
    assert all(v == [0.1, 0.2] for v in vectors)
    # 超过单批上限时切成两次请求
    assert len(calls) == 2
    assert len(calls[0]) == vs._TEI_EMBED_BATCH and len(calls[1]) == 3


def test_tei_rerank_maps_scores_by_index():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rerank"
        # TEI 按分数降序返回 {index, score};客户端须映射回原文本顺序
        return httpx.Response(200, json=[
            {"index": 2, "score": 0.9},
            {"index": 0, "score": 0.5},
            {"index": 1, "score": 0.1},
        ])

    client = vs.TeiRerankClient("http://tei-rr", transport=httpx.MockTransport(handler))
    assert client.rerank_scores("q", ["a", "b", "c"]) == [0.5, 0.1, 0.9]


# ── 显式向量路径(fake collection + fake embedder)────────────────────────────

class _FakeCollection:
    def __init__(self):
        self.added = None
        self.queried = None

    def get(self, **kwargs):
        return {"ids": []}

    def add(self, **kwargs):
        self.added = kwargs

    def query(self, **kwargs):
        self.queried = kwargs
        return {"ids": [[]]}


class _FakeEmbedder:
    def embed(self, texts):
        return [[float(len(t)), 1.0] for t in texts]


def _storage_with_fakes(tmp_path):
    storage = vs.ChromaVectorStorage(db_path=str(tmp_path / "chroma"))
    storage._collection = _FakeCollection()
    storage._embedder = _FakeEmbedder()
    storage._client = object()
    return storage


def test_save_passes_explicit_embeddings(tmp_path):
    storage = _storage_with_fakes(tmp_path)
    item = GenericContent(
        id="a1", title="T", source_url="https://example.com/a1",
        publish_date="2026-07-23", source_id="rss_arxiv_cs_ai",
        content="正文" * 60,
    )
    assert asyncio.run(storage.save(item)) is True

    added = storage._collection.added
    assert added is not None
    assert len(added["embeddings"]) == len(added["documents"]) == len(added["ids"])
    # 向量确由 provider 计算(维度=2 的 fake 向量)
    assert all(len(v) == 2 for v in added["embeddings"])


def test_search_uses_query_embeddings(tmp_path):
    storage = _storage_with_fakes(tmp_path)
    asyncio.run(storage.search("找点什么", n_results=3))

    queried = storage._collection.queried
    assert queried is not None
    assert "query_texts" not in queried
    assert queried["query_embeddings"] == [[4.0, 1.0]]
    assert queried["n_results"] == 3


# ── 重排 provider 选择 ───────────────────────────────────────────────────────

def test_rerank_skips_gracefully_when_remote_without_rerank_url(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "settings", replace(
        vs.settings,
        rag=replace(vs.settings.rag, enabled=True, chroma_url="http://chroma:8000",
                    embedding_url="http://tei-embed:80", rerank_url=""),
    ))
    storage = _storage_with_fakes(tmp_path)
    candidates = [{"document": "d1", "distance": 0.3}, {"document": "d2", "distance": 0.1}]
    result = asyncio.run(storage.rerank("q", list(candidates)))
    # 未配 rerank_url:原样返回、不加 rerank_score、哨兵置 False 不再重试
    assert result == candidates
    assert storage._reranker is False
    assert all("rerank_score" not in c for c in result)


def test_rerank_uses_tei_when_rerank_url_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "settings", replace(
        vs.settings,
        rag=replace(vs.settings.rag, enabled=True, chroma_url="http://chroma:8000",
                    embedding_url="http://tei-embed:80", rerank_url="http://tei-rr:80"),
    ))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"index": 0, "score": 0.2}, {"index": 1, "score": 0.8}])

    storage = _storage_with_fakes(tmp_path)
    storage._reranker = vs.TeiRerankClient("http://tei-rr", transport=httpx.MockTransport(handler))
    result = asyncio.run(storage.rerank("q", [
        {"document": "d1", "distance": 0.3},
        {"document": "d2", "distance": 0.1},
    ]))
    assert [c["document"] for c in result] == ["d2", "d1"]
    assert result[0]["rerank_score"] == 0.8


# ── 远程模式配置校验 ─────────────────────────────────────────────────────────

def test_remote_mode_requires_embedding_url(tmp_path, monkeypatch):
    monkeypatch.setattr(vs, "settings", replace(
        vs.settings,
        rag=replace(vs.settings.rag, enabled=True, chroma_url="http://chroma:8000",
                    embedding_url="", rerank_url=""),
    ))
    monkeypatch.setattr(vs.chromadb, "HttpClient", lambda **kwargs: object())
    storage = vs.ChromaVectorStorage(db_path=str(tmp_path / "chroma"))
    try:
        storage._load_collection()
        raised = False
    except RuntimeError as exc:
        raised = True
        assert "embedding_url" in str(exc)
    assert raised
