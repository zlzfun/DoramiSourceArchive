# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

### Backend (Python)

```bash
# Install dependencies (use uv, the project uses uv.lock)
uv sync

# Run the backend server (starts on http://127.0.0.1:8088, hot-reload enabled)
python src/main.py

# API docs available at http://127.0.0.1:8088/docs
```

### Frontend (React + Vite + Tailwind CSS v4)

```bash
cd frontend
npm install
npm run dev      # Start dev server (port 5173, proxies /api → backend)
npm run build    # Production build
npm run lint     # ESLint
```

Data is stored in the `data/` directory (SQLite `cms_data.db` and ChromaDB `chroma_db/`).

## Architecture Overview

**DoramiSourceArchive** (哆啦美·归档中枢) is an AI content aggregation CMS with RAG capabilities. It fetches content from multiple sources, stores it in SQLite, and builds a vector index in ChromaDB for semantic search.

### Core Data Flow

```
Fetcher → DataPipeline → DatabaseStorage (SQLite via SQLModel)
                                    ↓ (explicit separate step)
                         ChromaVectorStorage (ChromaDB + sentence-transformers)
```

**Important**: `DataPipeline` is initialised with only `db_sink` — vectorization into ChromaDB is never automatic. It must be triggered explicitly via `POST /api/vectorize/{article_id}` or the batch endpoint.

### Key Design Decisions

**Dual-dimension content identity**: Every piece of content carries both `content_type` (data shape — `arxiv`, `wechat_article`, `tech_conference`, etc.) and `source_id` (which channel produced it — `wechat_jiqizhixin`, `webhook_dify_workflow`, etc.).

**Plugin-based fetcher discovery**: `FetcherRegistry` (`src/fetchers/registry.py`) auto-scans `src/fetchers/impl/` for `BaseFetcher` subclasses at import time. Any new fetcher placed there with `source_id`, `content_type`, `name`, `icon`, and `description` class attributes is automatically registered and surfaced in the frontend. The frontend dynamically renders parameter forms based on `get_parameter_schema()`, whose return format is:
```python
[{"field": "limit", "label": "单次获取上限", "type": "number", "default": 5}]
```

**`extensions_json` serialization pattern**: `serialize_to_metadata()` splits a content object's fields into base fields (from `BaseContent`) and subclass-specific extension fields. The extensions are serialised as a JSON string into the `ArticleRecord.extensions_json` column. When reconstructing for vectorization, a `GenericContent` object is used since the ORM only stores the flat record.

**WeChat Official Account auth flow**: `src/fetchers/impl/wechat_gzh_fetcher.py` uses Playwright to automate browser-based QR code login. Credentials are cached to `.wechat_auth/wechat_config.json`. Error code 200003 (expired credentials) triggers self-healing re-authentication. A global `asyncio.Lock` prevents concurrent login sessions.

**Vector chunking & cleaning**: Text is cleaned via `clean_text()` (HTML stripping, HN boilerplate removal, arxiv prefix removal) then split into paragraph-aware 800-char chunks with 150-char overlap. Articles with `< 30` usable characters are indexed with a header-only chunk. Metadata headers (source name, date, title) are prepended to every chunk to support temporal and source queries. Each chunk is an independent ChromaDB document linked by `parent_id` metadata. Semantic search fetches `top_k * 4` raw chunks then deduplicates by `parent_id`.

**Embedding model**: Default is `BAAI/bge-m3` (multilingual, supports Chinese queries against English documents). Override with `LOCAL_MODEL_PATH`. Changing models requires `POST /api/vector/reindex-all` to rebuild the collection from scratch.

### Project Structure

```
src/
├── main.py                  # Entry point: starts uvicorn with reload=True
├── api/app.py               # FastAPI app — all REST endpoints + APScheduler init
├── models/
│   ├── content.py           # Dataclass content models (BaseContent + 7 subtypes)
│   └── db.py                # SQLModel ORM tables (ArticleRecord, FetchTaskRecord)
├── fetchers/
│   ├── base.py              # BaseFetcher: httpx client, retries (_safe_get/_safe_post), template method
│   ├── registry.py          # FetcherRegistry singleton — auto-discovers impl/ on import
│   └── impl/
│       ├── wechat_gzh_fetcher.py  # WeChat Official Account (Playwright auth + API scraping)
│       └── webhook_trigger.py     # Outbound Dify workflow trigger (not a content source)
├── pipeline/core.py         # DataPipeline: drives fetcher → broadcasts to registered storages
└── storage/
    ├── base.py              # BaseStorage abstract class
    └── impl/
        ├── db_storage.py    # SQLite storage (also exposes mark_as_vectorized/unvectorized)
        └── vector_storage.py # ChromaDB storage with chunking + sentence-transformers

frontend/src/
├── api.js                   # All fetch() calls to the backend (single source of truth)
├── App.jsx                  # Root: tab routing between DataTab / FetchTab / VectorTab
└── components/
    ├── DataTab.jsx          # 知识台账: article list, filters, CRUD
    ├── FetchTab.jsx         # 节点与调度: fetcher triggers + cron task management
    ├── VectorTab.jsx        # 向量雷达: semantic search UI
    ├── ManualAddModal.jsx   # Manual article entry form
    ├── ArticleDetailModal.jsx
    ├── DateRangePicker.jsx
    └── Toast.jsx
```

### Key Endpoints

**Articles**
- `GET /api/articles` — list/query (filters: `content_type`, `source_id`, `is_vectorized`, `search`, `publish_date_start/end`, `fetched_date_start/end`, `skip`, `limit`)
- `POST /api/articles` — manual entry
- `PUT /api/articles/{id}` — update (editing `content` or `title` resets `is_vectorized` and purges vector chunks)
- `DELETE /api/articles/{id}` — delete (also purges vector chunks if vectorized)
- `POST /api/articles/batch-delete`

**Fetchers & Tasks**
- `GET /api/fetchers` — list all discovered fetchers with parameter schemas
- `POST /api/fetch/{fetcher_id}` — trigger a specific fetcher
- `GET/POST/DELETE /api/tasks` — cron-scheduled task CRUD (APScheduler)

**Vectorization**
- `POST /api/vectorize/{article_id}` — vectorize single article
- `POST /api/vectorize/batch`
- `POST /api/vectorize/all-pending` — vectorize all `is_vectorized=False` articles
- `POST /api/vector/search` — semantic search (`query`, `top_k`, optional `content_type`/`source_id`/`publish_date_gte` filters)
- `GET /api/vector/stats` — total chunk count
- `POST /api/vector/reindex-all` — delete and rebuild entire ChromaDB collection, then re-vectorize all articles
- `DELETE /api/vector/{article_id}` — delete vector chunks only (keeps DB record, resets `is_vectorized`)
- `POST /api/vector/batch-delete`

**RAG**
- `POST /api/rag/context` — assemble ranked context string for downstream LLM apps (Dify etc.); body: `RagContextQuery` (`query`, `top_k`, `max_chars`, `score_threshold`, `content_type`, `source_id`, `publish_date_gte`, `context_separator`)
- `GET /api/rag/similar/{article_id}` — find semantically similar articles by re-querying with the article's own text

### RAG Evaluation

Offline evaluation harness in `tests/rag/`:

```bash
# Run all test cases against the live ChromaDB (requires data/ to be populated)
.venv/bin/python tests/rag/evaluate.py

# Only run cases tagged with a specific capability flag
.venv/bin/python tests/rag/evaluate.py --tag-filter T6

# Preview test cases without running retrieval
.venv/bin/python tests/rag/evaluate.py --dry-run
```

Test sets are versioned JSON files (`testset_v1.json`, etc.) with 25+ cases across categories:  
A (by source), B (by product/tool), C (semantic), D (metadata/empty content), E (temporal), F (cross-lingual).  
Results are saved to `tests/rag/results/eval_<timestamp>.json` (gitignored).

### Environment Variables

| Variable | Purpose |
|---|---|
| `HF_ENDPOINT` | HuggingFace mirror (defaults to `https://hf-mirror.com` in `main.py`) |
| `LOCAL_MODEL_PATH` | Path to local sentence-transformers model for offline embedding; defaults to `BAAI/bge-m3` |
| `XIAOLUBAN_AUTH` / `XIAOLUBAN_RECEIVER` | Internal notification bot credentials for WeChat QR code alerts |
