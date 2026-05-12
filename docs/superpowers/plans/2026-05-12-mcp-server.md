# MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Streamable HTTP MCP server (5 tools) to DoramiSourceArchive, mounted at `/mcp` in the existing FastAPI app, with a soft enable/disable switch and a frontend management panel.

**Architecture:** `FastMCP` (official `mcp` SDK) is constructed in `src/mcp_server.py` via a `build_mcp_app(db_sink, vector_sink)` factory. Tool logic lives in module-level helper functions for testability; closures in `build_mcp_app` delegate to them. The resulting ASGI app is wrapped by `MCPGateApp` (an inline ASGI wrapper that returns 503 when disabled) and mounted at `/mcp` in `app.py`. The `mcp_enabled` flag is persisted in a new `AppSettingRecord` SQLite row and cached as a module-level bool.

**Tech Stack:** `mcp>=1.0.0` (FastMCP, Streamable HTTP), FastAPI/Starlette ASGI mounting, SQLModel, React + Lucide icons

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `pyproject.toml` | Modify | Add `mcp>=1.0.0` dependency |
| `src/models/db.py` | Modify | Add `AppSettingRecord` table |
| `src/mcp_server.py` | **Create** | Helper functions + `build_mcp_app()` factory exposing 5 MCP tools |
| `src/api/app.py` | Modify | `MCPGateApp` wrapper, mount at `/mcp`, startup init, `/api/mcp/status` + `/api/mcp/toggle` |
| `tests/test_mcp.py` | **Create** | Tests for helper functions + REST endpoints |
| `frontend/src/api.js` | Modify | Add `fetchMcpStatus`, `toggleMcp` |
| `frontend/src/components/MCPTab.jsx` | **Create** | MCP management panel |
| `frontend/src/App.jsx` | Modify | Add MCP tab to navigation |

---

## Field Reference (avoid guessing)

- `ArticleRecord.id` → `str` (primary key)
- `ArticleRecord.source_url` (not `url`)
- `ArticleRecord.fetched_date` (not `fetched_at`)
- `vector_sink.search(query, n_results, content_type, source_id, publish_date_gte, publish_date_lte, days_ago)` → `async`, returns `List[{"id": chunk_id, "document": str, "metadata": {"parent_id", "source_id", "content_type", "publish_date", "title", "source_name", ...}, "distance": float}]`
- Distance is cosine distance: **lower = more relevant**; threshold default `1.5`
- `fetcher_registry.get_all_metadata()` returns `[{"id", "name", "icon", "desc", "category", "content_type", ...}]`
- `SOURCE_FRIENDLY_NAMES` dict lives in `storage.impl.vector_storage`

---

### Task 1: Add `mcp` dependency and verify ASGI API

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add dependency**

Open `pyproject.toml` and add `"mcp>=1.0.0",` to the `dependencies` list.

- [ ] **Step 2: Sync and discover the exact ASGI method name**

```bash
uv sync
uv run python -c "
from mcp.server.fastmcp import FastMCP
mcp = FastMCP('test')
candidates = [m for m in dir(mcp) if any(k in m.lower() for k in ('http', 'asgi', 'app', 'starlette'))]
print('ASGI candidates:', candidates)
"
```

The output will contain the exact method name (likely `streamable_http_app`, `get_asgi_app`, or similar). **Note it down** — you will use it as `_mcp_server.<method>()` in Task 5.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add mcp>=1.0.0"
```

---

### Task 2: Add `AppSettingRecord` model

**Files:**
- Modify: `src/models/db.py`
- Create: `tests/test_mcp.py`

- [ ] **Step 1: Write failing test**

Create `tests/__init__.py` (empty) and `tests/test_mcp.py`:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sqlmodel import create_engine, SQLModel, Session

def make_engine():
    """In-memory SQLite engine with all tables created."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


# ── Task 2 ────────────────────────────────────────────────────────────────────

def test_app_setting_crud():
    from models.db import AppSettingRecord
    engine = make_engine()
    with Session(engine) as s:
        s.add(AppSettingRecord(key="mcp_enabled", value="true"))
        s.commit()
    with Session(engine) as s:
        rec = s.get(AppSettingRecord, "mcp_enabled")
        assert rec is not None and rec.value == "true"
        rec.value = "false"
        s.add(rec)
        s.commit()
    with Session(engine) as s:
        rec = s.get(AppSettingRecord, "mcp_enabled")
        assert rec.value == "false"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_mcp.py::test_app_setting_crud -v
```

Expected: FAIL with `ImportError` or `AttributeError` — `AppSettingRecord` not defined yet.

- [ ] **Step 3: Add model to `src/models/db.py`**

Append at the end of `src/models/db.py`:

```python
class AppSettingRecord(SQLModel, table=True):
    __tablename__ = "app_settings"
    key: str = Field(primary_key=True)
    value: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_mcp.py::test_app_setting_crud -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/models/db.py tests/__init__.py tests/test_mcp.py
git commit -m "feat: add AppSettingRecord for MCP enable state persistence"
```

---

### Task 3: Create `src/mcp_server.py` with DB-based tools

**Files:**
- Create: `src/mcp_server.py`
- Modify: `tests/test_mcp.py`

This task implements `list_sources`, `browse_articles`, and `get_article`. All three are sync (no vector search).

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp.py`:

```python
# ── Task 3 helpers ────────────────────────────────────────────────────────────

from unittest.mock import MagicMock
from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage


def make_db_sink():
    sink = DatabaseStorage(db_url="sqlite:///:memory:")
    return sink


def make_vector_sink():
    return MagicMock(spec=ChromaVectorStorage)


def seed_article(db_sink, title="Test Article", source_id="test_src",
                 content_type="arxiv", content="hello world body"):
    from models.db import ArticleRecord
    import datetime
    rec = ArticleRecord(
        id=f"test_{title.replace(' ', '_')}",
        title=title,
        source_id=source_id,
        content_type=content_type,
        source_url="http://example.com",
        publish_date=str(datetime.date.today()),
        fetched_date=str(datetime.date.today()),
        has_content=bool(content),
        content=content,
        extensions_json='{"key": "val"}',
        is_vectorized=False,
    )
    with Session(db_sink.engine) as s:
        s.add(rec)
        s.commit()
    return rec.id


# ── Task 3 tests ──────────────────────────────────────────────────────────────

def test_list_sources_returns_list():
    from mcp_server import _list_sources_impl
    db = make_db_sink()
    result = _list_sources_impl(db)
    assert isinstance(result, list)
    # Each entry must have required keys
    for item in result:
        assert "source_id" in item
        assert "name" in item


def test_browse_articles_empty():
    from mcp_server import _browse_articles_impl
    db = make_db_sink()
    result = _browse_articles_impl(db)
    assert result == []


def test_browse_articles_filter_by_source():
    from mcp_server import _browse_articles_impl
    db = make_db_sink()
    seed_article(db, title="Match", source_id="src_a")
    seed_article(db, title="No Match", source_id="src_b")
    result = _browse_articles_impl(db, source_id="src_a")
    assert len(result) == 1
    assert result[0]["title"] == "Match"
    assert result[0]["source_url"] == "http://example.com"


def test_get_article_found():
    from mcp_server import _get_article_impl
    db = make_db_sink()
    article_id = seed_article(db, title="Full Article", content="long body text")
    result = _get_article_impl(db, article_id)
    assert isinstance(result, dict)
    assert result["title"] == "Full Article"
    assert result["content"] == "long body text"
    assert result["extensions"] == {"key": "val"}


def test_get_article_not_found():
    from mcp_server import _get_article_impl
    db = make_db_sink()
    result = _get_article_impl(db, "nonexistent_id")
    assert isinstance(result, dict)
    assert "error" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_mcp.py::test_list_sources_returns_list tests/test_mcp.py::test_browse_articles_empty -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'mcp_server'`

- [ ] **Step 3: Create `src/mcp_server.py`**

```python
from __future__ import annotations
import json
from typing import Optional
from mcp.server.fastmcp import FastMCP
from sqlmodel import Session, select
from models.db import ArticleRecord, SourceStateRecord
from storage.impl.db_storage import DatabaseStorage
from storage.impl.vector_storage import ChromaVectorStorage, SOURCE_FRIENDLY_NAMES
from fetchers.registry import fetcher_registry


# ── Helper functions (testable independently) ─────────────────────────────────

def _list_sources_impl(db_sink: DatabaseStorage) -> list[dict]:
    fetchers = fetcher_registry.get_all_metadata()
    with Session(db_sink.engine) as session:
        states = {s.fetcher_id: s for s in session.exec(select(SourceStateRecord)).all()}
    result = []
    for f in fetchers:
        state = states.get(f["id"])
        result.append({
            "source_id": f["id"],
            "name": f["name"],
            "icon": f.get("icon", ""),
            "content_type": f.get("content_type", ""),
            "category": f.get("category", "general"),
            "last_fetch_time": state.last_completed_at if state else None,
            "last_fetch_status": state.status if state else None,
        })
    return result


def _browse_articles_impl(
    db_sink: DatabaseStorage,
    source_id: Optional[str] = None,
    content_type: Optional[str] = None,
    publish_date_start: Optional[str] = None,
    publish_date_end: Optional[str] = None,
    has_content: Optional[bool] = None,
    limit: int = 20,
    skip: int = 0,
) -> list[dict]:
    effective_limit = min(limit, 100)
    with Session(db_sink.engine) as session:
        q = select(ArticleRecord)
        if source_id:
            q = q.where(ArticleRecord.source_id == source_id)
        if content_type:
            q = q.where(ArticleRecord.content_type == content_type)
        if publish_date_start:
            q = q.where(ArticleRecord.publish_date >= publish_date_start)
        if publish_date_end:
            end = publish_date_end if "T" in publish_date_end else f"{publish_date_end}T23:59:59"
            q = q.where(ArticleRecord.publish_date <= end)
        if has_content is True:
            q = q.where(ArticleRecord.has_content == True)
        elif has_content is False:
            q = q.where(ArticleRecord.has_content == False)
        q = q.order_by(ArticleRecord.publish_date.desc()).offset(skip).limit(effective_limit)
        records = session.exec(q).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "source_id": r.source_id,
            "content_type": r.content_type,
            "publish_date": r.publish_date,
            "source_url": r.source_url,
            "is_vectorized": r.is_vectorized,
        }
        for r in records
    ]


def _get_article_impl(db_sink: DatabaseStorage, article_id: str) -> dict:
    with Session(db_sink.engine) as session:
        record = session.get(ArticleRecord, article_id)
    if not record:
        return {"error": f"Article '{article_id}' not found."}
    return {
        "id": record.id,
        "title": record.title,
        "content": record.content or "",
        "source_id": record.source_id,
        "content_type": record.content_type,
        "publish_date": record.publish_date,
        "source_url": record.source_url,
        "extensions": json.loads(record.extensions_json or "{}"),
    }


async def _search_articles_impl(
    vector_sink: ChromaVectorStorage,
    query: str,
    top_k: int = 10,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    publish_date_gte: Optional[str] = None,
    distance_threshold: float = 1.5,
) -> list[dict]:
    raw = await vector_sink.search(
        query,
        n_results=top_k * 4,
        content_type=content_type,
        source_id=source_id,
        publish_date_gte=publish_date_gte,
    )
    best: dict[str, dict] = {}
    for chunk in raw:
        if chunk["distance"] > distance_threshold:
            continue
        pid = chunk["metadata"].get("parent_id", chunk["id"])
        if pid not in best or chunk["distance"] < best[pid]["distance"]:
            best[pid] = chunk
    ranked = sorted(best.values(), key=lambda x: x["distance"])[:top_k]
    return [
        {
            "id": item["metadata"].get("parent_id", item["id"]),
            "title": item["metadata"].get("title", ""),
            "source_id": item["metadata"].get("source_id", ""),
            "content_type": item["metadata"].get("content_type", ""),
            "publish_date": item["metadata"].get("publish_date", ""),
            "summary": item["document"][:200] if item.get("document") else "",
            "distance": round(item["distance"], 4),
        }
        for item in ranked
    ]


async def _get_rag_context_impl(
    db_sink: DatabaseStorage,
    vector_sink: ChromaVectorStorage,
    query: str,
    top_k: int = 8,
    max_chars: int = 4000,
    distance_threshold: float = 1.5,
    content_type: Optional[str] = None,
    source_id: Optional[str] = None,
    publish_date_gte: Optional[str] = None,
    context_separator: str = "\n\n---\n\n",
) -> str:
    raw = await vector_sink.search(
        query,
        n_results=top_k * 4,
        content_type=content_type,
        source_id=source_id,
        publish_date_gte=publish_date_gte,
    )
    best: dict[str, dict] = {}
    for chunk in raw:
        if chunk["distance"] > distance_threshold:
            continue
        pid = chunk["metadata"].get("parent_id", chunk["id"])
        if pid not in best or chunk["distance"] < best[pid]["distance"]:
            best[pid] = chunk

    ranked = sorted(best.values(), key=lambda x: x["distance"])[:top_k]

    parts = []
    total_chars = 0
    for rank, res in enumerate(ranked, start=1):
        pid = res["metadata"].get("parent_id", res["id"])
        record = await db_sink.get(pid)
        source_id_val = res["metadata"].get("source_id", "")
        source_name = SOURCE_FRIENDLY_NAMES.get(source_id_val, source_id_val)
        pub_date = res["metadata"].get("publish_date", "")
        title = record.title if record else res["metadata"].get("title", "")
        source_url = record.source_url if record else ""
        raw_doc = res.get("document", "")
        body_start = raw_doc.find("\n\n")
        excerpt = raw_doc[body_start + 2:].strip() if body_start != -1 else raw_doc.strip()
        block = (
            f"[{rank}] 来源: {source_name} | 日期: {pub_date}\n"
            f"标题: {title}\n"
            f"链接: {source_url}\n\n"
            f"{excerpt}"
        )
        if total_chars + len(block) > max_chars and parts:
            break
        parts.append(block)
        total_chars += len(block)

    return context_separator.join(parts)


# ── FastMCP server factory ────────────────────────────────────────────────────

def build_mcp_app(db_sink: DatabaseStorage, vector_sink: ChromaVectorStorage) -> FastMCP:
    mcp = FastMCP(
        "dorami-archive",
        description="哆啦美·归档中枢 MCP Server — AI资讯检索与RAG上下文组装",
    )

    @mcp.tool()
    def list_sources() -> list[dict]:
        """列出平台中所有已知数据来源。
        调用 browse_articles 或 search_articles 前先用本工具了解可用的 source_id 和 content_type。
        List all known data sources in the archive. Call this first to discover
        valid source_id and content_type values for other tools.
        """
        return _list_sources_impl(db_sink)

    @mcp.tool()
    def browse_articles(
        source_id: Optional[str] = None,
        content_type: Optional[str] = None,
        publish_date_start: Optional[str] = None,
        publish_date_end: Optional[str] = None,
        has_content: Optional[bool] = None,
        limit: int = 20,
        skip: int = 0,
    ) -> list[dict]:
        """按条件过滤浏览文章列表，适合「Anthropic最新动态」或生成日报等场景。
        Filter and browse articles by metadata. Use for source-specific or date-range queries.
        Scenarios: 「某来源最新资讯」「生成今日日报」「列出某类型内容」
        publish_date_start/end: YYYY-MM-DD. limit max 100.
        """
        return _browse_articles_impl(
            db_sink, source_id=source_id, content_type=content_type,
            publish_date_start=publish_date_start, publish_date_end=publish_date_end,
            has_content=has_content, limit=limit, skip=skip,
        )

    @mcp.tool()
    def get_article(article_id: str) -> dict:
        """按 ID 获取单篇文章完整内容（含正文、extensions 元数据）。
        Get full content of a single article by its ID.
        Use article IDs from browse_articles or search_articles results.
        extensions contains content-type-specific metadata parsed from JSON.
        """
        return _get_article_impl(db_sink, article_id)

    @mcp.tool()
    async def search_articles(
        query: str,
        top_k: int = 10,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        publish_date_gte: Optional[str] = None,
        distance_threshold: float = 1.5,
    ) -> list[dict]:
        """语义向量搜索文章，支持中英文，按相关性排序。适合主题查询场景。
        Semantic vector search. Supports Chinese and English queries.
        Scenarios: 「最近的具身智能资讯有哪些？」「Find papers on multimodal LLMs」
        distance_threshold: cosine distance cutoff (lower = stricter, default 1.5).
        publish_date_gte: YYYY-MM-DD. Returns articles ranked by relevance (distance asc).
        """
        return await _search_articles_impl(
            vector_sink, query=query, top_k=top_k,
            content_type=content_type, source_id=source_id,
            publish_date_gte=publish_date_gte, distance_threshold=distance_threshold,
        )

    @mcp.tool()
    async def get_rag_context(
        query: str,
        top_k: int = 8,
        max_chars: int = 4000,
        distance_threshold: float = 1.5,
        content_type: Optional[str] = None,
        source_id: Optional[str] = None,
        publish_date_gte: Optional[str] = None,
        context_separator: str = "\n\n---\n\n",
    ) -> str:
        """语义检索后组装格式化RAG上下文字符串，可直接拼入LLM System Prompt。
        Assemble a formatted RAG context string ready to inject into an LLM prompt.
        Scenarios: 需要用归档资讯回答用户提问时的上下文准备。
        Returns empty string when no relevant results found.
        publish_date_gte: YYYY-MM-DD. distance_threshold: cosine distance cutoff (default 1.5).
        """
        return await _get_rag_context_impl(
            db_sink, vector_sink, query=query, top_k=top_k,
            max_chars=max_chars, distance_threshold=distance_threshold,
            content_type=content_type, source_id=source_id,
            publish_date_gte=publish_date_gte, context_separator=context_separator,
        )

    return mcp
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_mcp.py::test_list_sources_returns_list \
  tests/test_mcp.py::test_browse_articles_empty \
  tests/test_mcp.py::test_browse_articles_filter_by_source \
  tests/test_mcp.py::test_get_article_found \
  tests/test_mcp.py::test_get_article_not_found -v
```

Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add src/mcp_server.py tests/test_mcp.py
git commit -m "feat: add mcp_server with list_sources, browse_articles, get_article"
```

---

### Task 4: Add and test vector-based tools

**Files:**
- Modify: `tests/test_mcp.py`

The vector tool implementations are already in `src/mcp_server.py` from Task 3. This task writes and runs their tests.

- [ ] **Step 1: Write failing tests**

Append to `tests/test_mcp.py`:

```python
# ── Task 4 ────────────────────────────────────────────────────────────────────
import asyncio


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_search_articles_empty_index():
    from mcp_server import _search_articles_impl
    vec = make_vector_sink()
    vec.search.return_value = []
    result = run(_search_articles_impl(vec, query="embodied intelligence"))
    assert result == []
    vec.search.assert_called_once()


def test_search_articles_deduplicates_chunks():
    from mcp_server import _search_articles_impl
    vec = make_vector_sink()
    # Two chunks from the same parent_id — only the closer one should win
    vec.search.return_value = [
        {"id": "art1_chunk_0", "document": "chunk A", "distance": 0.3,
         "metadata": {"parent_id": "art1", "title": "Robot Survey",
                      "source_id": "arxiv_src", "content_type": "arxiv",
                      "publish_date": "2025-03-01"}},
        {"id": "art1_chunk_1", "document": "chunk B", "distance": 0.8,
         "metadata": {"parent_id": "art1", "title": "Robot Survey",
                      "source_id": "arxiv_src", "content_type": "arxiv",
                      "publish_date": "2025-03-01"}},
    ]
    result = run(_search_articles_impl(vec, query="robots"))
    assert len(result) == 1
    assert result[0]["id"] == "art1"
    assert result[0]["distance"] == 0.3


def test_search_articles_filters_by_threshold():
    from mcp_server import _search_articles_impl
    vec = make_vector_sink()
    vec.search.return_value = [
        {"id": "art2_chunk_0", "document": "irrelevant", "distance": 1.8,
         "metadata": {"parent_id": "art2", "title": "Off-topic",
                      "source_id": "other", "content_type": "misc",
                      "publish_date": "2025-01-01"}},
    ]
    result = run(_search_articles_impl(vec, query="something", distance_threshold=1.5))
    assert result == []


def test_get_rag_context_empty():
    from mcp_server import _get_rag_context_impl
    db = make_db_sink()
    vec = make_vector_sink()
    vec.search.return_value = []
    result = run(_get_rag_context_impl(db, vec, query="test"))
    assert result == ""


def test_get_rag_context_formats_block():
    from mcp_server import _get_rag_context_impl
    db = make_db_sink()
    article_id = seed_article(db, title="Embodied AI Survey", content="Survey content here.")
    vec = make_vector_sink()
    vec.search.return_value = [
        {"id": f"{article_id}_chunk_0",
         "document": "Header line\n\nSurvey content here.",
         "distance": 0.4,
         "metadata": {"parent_id": article_id, "title": "Embodied AI Survey",
                      "source_id": "test_src", "content_type": "arxiv",
                      "publish_date": "2025-03-01"}},
    ]
    result = run(_get_rag_context_impl(db, vec, query="embodied AI"))
    assert "Embodied AI Survey" in result
    assert "2025-03-01" in result
    assert isinstance(result, str)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_mcp.py::test_search_articles_empty_index -v
```

Expected: FAIL — `AttributeError` because `vec.search` is a `MagicMock` but `vector_sink.search` is a coroutine. Fix: configure the mock as an async mock.

If search returns a coroutine error, update `make_vector_sink()` in `tests/test_mcp.py` to use `AsyncMock` for the `search` method:

```python
from unittest.mock import MagicMock, AsyncMock

def make_vector_sink():
    sink = MagicMock(spec=ChromaVectorStorage)
    sink.search = AsyncMock()
    return sink
```

Re-run `test_search_articles_empty_index` — it should now fail with an import error or a different assertion failure showing the function exists but the test assertion fails for a different reason. Continue to Step 3.

- [ ] **Step 3: Run all vector tool tests**

```bash
uv run pytest tests/test_mcp.py::test_search_articles_empty_index \
  tests/test_mcp.py::test_search_articles_deduplicates_chunks \
  tests/test_mcp.py::test_search_articles_filters_by_threshold \
  tests/test_mcp.py::test_get_rag_context_empty \
  tests/test_mcp.py::test_get_rag_context_formats_block -v
```

Expected: All PASS. If `get_rag_context` fails on `await db_sink.get(pid)`: `DatabaseStorage.get()` is an async method but in tests `db_sink` is a real `DatabaseStorage` instance — this is fine as `asyncio.get_event_loop().run_until_complete` handles it.

- [ ] **Step 4: Commit**

```bash
git add tests/test_mcp.py
git commit -m "test: add vector tool tests for search_articles and get_rag_context"
```

---

### Task 5: Wire MCP into `app.py`

**Files:**
- Modify: `src/api/app.py`
- Modify: `tests/test_mcp.py`

- [ ] **Step 1: Write failing REST endpoint tests**

Append to `tests/test_mcp.py`:

```python
# ── Task 5 ────────────────────────────────────────────────────────────────────
from fastapi.testclient import TestClient


def get_client():
    import api.app as app_module
    return TestClient(app_module.app)


def test_mcp_status_returns_correct_structure():
    client = get_client()
    resp = client.get("/api/mcp/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "url" in data
    assert data["url"].endswith("/mcp")
    assert "tools" in data
    assert len(data["tools"]) == 5
    tool_names = {t["name"] for t in data["tools"]}
    assert tool_names == {"list_sources", "browse_articles", "get_article",
                          "search_articles", "get_rag_context"}


def test_mcp_toggle_flips_state():
    import api.app as app_module
    client = get_client()
    initial = client.get("/api/mcp/status").json()["enabled"]
    resp = client.post("/api/mcp/toggle")
    assert resp.status_code == 200
    assert resp.json()["enabled"] != initial
    # Restore
    client.post("/api/mcp/toggle")
    assert client.get("/api/mcp/status").json()["enabled"] == initial
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_mcp.py::test_mcp_status_returns_correct_structure -v
```

Expected: FAIL — `404 Not Found`

- [ ] **Step 3: Add `MCPGateApp` and module-level state to `src/api/app.py`**

Add these imports to the existing import block in `src/api/app.py`:

```python
from starlette.responses import JSONResponse as StarletteJSONResponse
from mcp_server import build_mcp_app
from models.db import AppSettingRecord
```

Add the module-level flag and `MCPGateApp` class **before** `app = FastAPI(...)`:

```python
_mcp_enabled: bool = True


class MCPGateApp:
    def __init__(self, asgi_app):
        self._app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket") and not _mcp_enabled:
            response = StarletteJSONResponse(
                {"detail": "MCP server is disabled"}, status_code=503
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)
```

- [ ] **Step 4: Build MCP ASGI app and mount it**

After the `pipeline = DataPipeline(storages=[db_sink])` line, add:

```python
_mcp_server = build_mcp_app(db_sink, vector_sink)
# Use the method name you discovered in Task 1 Step 2.
# Most likely: _mcp_server.streamable_http_app()
# Alternatives: _mcp_server.get_asgi_app() / _mcp_server.http_app()
_mcp_asgi = _mcp_server.streamable_http_app()
```

After the CORS middleware setup (after `app.add_middleware(...)`), add:

```python
app.mount("/mcp", MCPGateApp(_mcp_asgi))
```

Add the startup event handler (place near the top of the endpoint definitions):

```python
@app.on_event("startup")
async def _init_mcp_state():
    global _mcp_enabled
    with Session(db_sink.engine) as session:
        rec = session.get(AppSettingRecord, "mcp_enabled")
        if rec is None:
            session.add(AppSettingRecord(key="mcp_enabled", value="true"))
            session.commit()
            _mcp_enabled = True
        else:
            _mcp_enabled = rec.value.lower() == "true"
```

- [ ] **Step 5: Add REST endpoints to `src/api/app.py`**

Add this block near the end of the file:

```python
# ==================== MCP Server Management ====================

_MCP_TOOLS_MANIFEST = [
    {"name": "search_articles",
     "description": "语义向量搜索文章，支持中英文，可按日期/来源/类型过滤"},
    {"name": "browse_articles",
     "description": "按条件过滤浏览文章列表（来源、类型、日期区间），适合日报生成"},
    {"name": "get_article",
     "description": "按 ID 获取单篇文章完整内容（含正文）"},
    {"name": "list_sources",
     "description": "列出所有已知数据来源，获取可用的 source_id 和 content_type"},
    {"name": "get_rag_context",
     "description": "语义检索后组装格式化 RAG 上下文字符串，可直接拼入 LLM Prompt"},
]


@app.get("/api/mcp/status")
def get_mcp_status():
    return {
        "enabled": _mcp_enabled,
        "url": "http://127.0.0.1:8088/mcp",
        "tools": _MCP_TOOLS_MANIFEST,
    }


@app.post("/api/mcp/toggle")
def toggle_mcp():
    global _mcp_enabled
    _mcp_enabled = not _mcp_enabled
    with Session(db_sink.engine) as session:
        rec = session.get(AppSettingRecord, "mcp_enabled")
        if rec is None:
            rec = AppSettingRecord(key="mcp_enabled", value=str(_mcp_enabled).lower())
            session.add(rec)
        else:
            rec.value = str(_mcp_enabled).lower()
            session.add(rec)
        session.commit()
    return {"enabled": _mcp_enabled}
```

- [ ] **Step 6: Run all tests**

```bash
uv run pytest tests/test_mcp.py -v
```

Expected: All tests PASS. If `test_mcp_status_returns_correct_structure` fails due to startup events not running in TestClient, add `with TestClient(app_module.app) as client:` (context manager form triggers lifespan events).

- [ ] **Step 7: Commit**

```bash
git add src/api/app.py tests/test_mcp.py
git commit -m "feat: mount MCP server at /mcp with soft enable/disable and management endpoints"
```

---

### Task 6: Frontend — API functions, MCPTab, and App tab

**Files:**
- Modify: `frontend/src/api.js`
- Create: `frontend/src/components/MCPTab.jsx`
- Modify: `frontend/src/App.jsx`

- [ ] **Step 1: Add API functions to `frontend/src/api.js`**

Append to the end of `frontend/src/api.js`:

```js
export const fetchMcpStatus = () =>
  fetch(`${API_BASE_URL}/mcp/status`).then(r => r.json());

export const toggleMcp = () =>
  fetch(`${API_BASE_URL}/mcp/toggle`, { method: 'POST' }).then(r => r.json());
```

- [ ] **Step 2: Create `frontend/src/components/MCPTab.jsx`**

```jsx
import { useState, useEffect, useCallback } from 'react';
import { Plug2, Copy, Check, Circle } from 'lucide-react';
import { fetchMcpStatus, toggleMcp } from '../api';

const TOOL_CARDS = [
  {
    name: 'search_articles',
    params: 'query, top_k?, content_type?, source_id?, publish_date_gte?, distance_threshold?',
    desc: '语义向量搜索，支持中英文，按相关性排序。适合主题查询，如「最近的具身智能资讯」。',
  },
  {
    name: 'browse_articles',
    params: 'source_id?, content_type?, publish_date_start?, publish_date_end?, has_content?, limit?, skip?',
    desc: '条件过滤浏览，按来源/类型/日期区间筛选。适合「Anthropic最新动态」或生成日报。',
  },
  {
    name: 'get_article',
    params: 'article_id: str',
    desc: '按 ID 获取单篇文章完整内容（含正文和扩展元数据）。',
  },
  {
    name: 'list_sources',
    params: '(无参数)',
    desc: '列出所有数据来源，获取可用的 source_id 和 content_type，建议首先调用。',
  },
  {
    name: 'get_rag_context',
    params: 'query, top_k?, max_chars?, distance_threshold?, content_type?, source_id?, publish_date_gte?',
    desc: '组装格式化 RAG 上下文字符串，可直接拼入 LLM System Prompt。',
  },
];

export default function MCPTab({ showToast }) {
  const [status, setStatus] = useState(null);
  const [toggling, setToggling] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await fetchMcpStatus());
    } catch {
      showToast('无法获取 MCP 状态', 'error');
    }
  }, [showToast]);

  useEffect(() => { loadStatus(); }, [loadStatus]);

  const handleToggle = async () => {
    setToggling(true);
    try {
      const data = await toggleMcp();
      setStatus(prev => ({ ...prev, enabled: data.enabled }));
      showToast(data.enabled ? 'MCP Server 已启动' : 'MCP Server 已停止',
                data.enabled ? 'success' : 'info');
    } catch {
      showToast('切换失败，请重试', 'error');
    } finally {
      setToggling(false);
    }
  };

  const handleCopy = () => {
    if (!status?.enabled || !status?.url) return;
    navigator.clipboard.writeText(status.url).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const enabled = status?.enabled ?? false;

  return (
    <div className="space-y-6">
      {/* Status & Control */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-5 flex items-center gap-2">
          <Plug2 className="w-5 h-5 text-blue-600" />
          MCP Server 状态
        </h2>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {enabled ? (
              <span className="relative flex h-3 w-3">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                <span className="relative inline-flex rounded-full h-3 w-3 bg-emerald-500" />
              </span>
            ) : (
              <Circle className="w-3 h-3 text-red-400 fill-red-400" />
            )}
            <span className="font-semibold text-slate-700">
              {status === null
                ? '加载中...'
                : enabled
                ? 'MCP Server 运行中'
                : 'MCP Server 已停止'}
            </span>
          </div>
          <button
            onClick={handleToggle}
            disabled={toggling || status === null}
            className={`px-4 py-2 rounded-lg text-sm font-bold transition-all border disabled:opacity-50 ${
              enabled
                ? 'bg-red-50 text-red-600 hover:bg-red-100 border-red-200'
                : 'bg-emerald-50 text-emerald-700 hover:bg-emerald-100 border-emerald-200'
            }`}
          >
            {toggling ? '处理中...' : enabled ? '停止 MCP' : '启动 MCP'}
          </button>
        </div>
      </div>

      {/* MCP URL */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-4">接入地址</h2>
        <div
          className={`flex items-center gap-3 p-3 rounded-xl border transition-opacity ${
            enabled ? 'bg-slate-50 border-slate-200' : 'bg-slate-100 border-slate-200 opacity-50'
          }`}
        >
          <code className="flex-1 text-sm font-mono text-slate-700 select-all break-all">
            {status?.url ?? 'http://127.0.0.1:8088/mcp'}
          </code>
          <button
            onClick={handleCopy}
            disabled={!enabled}
            title={enabled ? '复制 URL' : 'MCP 当前未运行'}
            className="p-1.5 rounded-lg hover:bg-slate-200 transition-colors disabled:cursor-not-allowed shrink-0"
          >
            {copied
              ? <Check className="w-4 h-4 text-emerald-600" />
              : <Copy className="w-4 h-4 text-slate-500" />}
          </button>
        </div>
        {!enabled && (
          <p className="text-xs text-slate-400 mt-2">启动 MCP Server 后方可复制接入地址</p>
        )}
        <p className="text-xs text-slate-400 mt-2">
          在 Agent 或 Dify 中配置 MCP URL 后，即可调用以下工具查询归档内容。
        </p>
      </div>

      {/* Tools */}
      <div className="bg-white rounded-2xl border border-slate-200/60 shadow-sm p-6">
        <h2 className="text-lg font-bold text-slate-800 mb-4">
          可用工具
          <span className="ml-2 text-sm font-normal text-slate-400">({TOOL_CARDS.length} 个)</span>
        </h2>
        <div className="space-y-3">
          {TOOL_CARDS.map(tool => (
            <div key={tool.name} className="p-4 rounded-xl bg-slate-50 border border-slate-100">
              <div className="flex items-start justify-between gap-3 mb-1">
                <code className="text-sm font-bold text-blue-700">{tool.name}</code>
                <span className="text-[11px] text-slate-400 font-mono text-right leading-relaxed">
                  {tool.params}
                </span>
              </div>
              <p className="text-sm text-slate-600">{tool.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Add MCP tab to `frontend/src/App.jsx`**

In `frontend/src/App.jsx`, add to the imports:

```jsx
import MCPTab from './components/MCPTab';
```

Update the existing `import { Database, CloudDownload, BarChart2, Activity, Bot, History } from 'lucide-react';` line to also include `Plug2`:

```jsx
import { Database, CloudDownload, BarChart2, Activity, Bot, History, Plug2 } from 'lucide-react';
```

In the `tabs` array, append the new entry after the `vector` tab:

```jsx
{ id: 'mcp', icon: Plug2, label: 'MCP 接入' },
```

In the `<main>` section, after the vector tab render:

```jsx
{activeTab === 'mcp' && <MCPTab showToast={showToast} />}
```

- [ ] **Step 4: Verify in browser**

Start backend and frontend:

```bash
# Terminal 1
uv run python src/main.py

# Terminal 2
cd frontend && npm run dev
```

Open `http://localhost:5173`. Click **MCP 接入** tab and verify:

1. Green pulsing dot + "MCP Server 运行中" visible
2. URL `http://127.0.0.1:8088/mcp` shown; copy button copies it
3. Click **停止 MCP** → indicator turns red, URL greyed out, copy disabled, toast shown
4. Click **启动 MCP** → indicator turns green again, URL interactive
5. All 5 tool cards visible with names and descriptions
6. Refresh page → state persists (enabled/disabled survives reload)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api.js frontend/src/components/MCPTab.jsx frontend/src/App.jsx
git commit -m "feat: add MCP management panel (MCPTab) to frontend"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|-----------------|------|
| Streamable HTTP MCP | Task 1 (mcp SDK), Task 5 (mount at /mcp) |
| Co-located with backend | Task 5 (same FastAPI app, same port) |
| Auto-starts with backend | Task 5 (`_mcp_enabled=True` by default, mounted at module load) |
| Soft start/stop (mcp_enabled flag) | Task 5 (MCPGateApp + toggle endpoint + AppSettingRecord) |
| State persists across restarts | Task 5 (AppSettingRecord in SQLite, loaded on startup) |
| 5 tools: list_sources | Task 3 |
| 5 tools: browse_articles | Task 3 |
| 5 tools: get_article | Task 3 |
| 5 tools: search_articles | Task 3 (impl) + Task 4 (tests) |
| 5 tools: get_rag_context | Task 3 (impl) + Task 4 (tests) |
| Tool descriptions with scenario examples | Task 3 (docstrings) |
| Date params as YYYY-MM-DD | Task 3 (all date params) |
| browse_articles limit capped at 100 | Task 3 (`min(limit, 100)`) |
| extensions parsed to object | Task 3 (`json.loads(extensions_json)`) |
| Frontend status panel | Task 6 (MCPTab ① block) |
| Frontend URL copy | Task 6 (MCPTab ② block) |
| Frontend tool list | Task 6 (MCPTab ③ block) |
| Frontend toggle button | Task 6 (MCPTab ① button → POST /api/mcp/toggle) |
| URL greyed when disabled | Task 6 (conditional `opacity-50`) |
| Toast feedback on toggle | Task 6 (`showToast` call) |

All requirements covered. No gaps found.

**Type consistency check:** `ArticleRecord.id` is `str` throughout — `get_article(article_id: str)` ✓, `seed_article` returns `rec.id` (str) ✓, `session.get(ArticleRecord, article_id)` accepts str ✓.

**Field names:** `source_url` used (not `url`) ✓. `fetched_date` not referenced in MCP tools ✓.

**Async tools:** `search_articles` and `get_rag_context` are `async def` ✓. `list_sources`, `browse_articles`, `get_article` are sync ✓.
