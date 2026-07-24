"""
ChromaDB 向量存储实现 (src/storage/impl/vector_storage.py)

T1: 默认模型升级为 BAAI/bge-m3（多语言，支持中文查询检索英文文档）
T2: 每个 chunk 前置元数据头部（来源/日期/标题），支持时效性与来源类查询
T6: 空正文内容回退——无正文文章仍可通过标题+头部建立索引，不再被跳过
T8: 入库前文本清洗（HTML 剥离 / HN 样板去除 / arxiv 前缀剥离 / 短无效内容剔除）
"""

import asyncio
import os
import re
import threading
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse
import chromadb
import httpx
from storage.base import BaseStorage
from models.content import BaseContent
from config import settings


# ── 嵌入/重排 provider(v3.17 服务化:进程内推理与 TEI HTTP 双模)─────────────
#
# 统一契约:嵌入向量一律由 provider 显式计算,再以 embeddings= 传给 chroma 的
# add/query——collection 不再挂 embedding_function,嵌入/远程两模式走同一条代码路径。
# provider 方法均为阻塞式(调用方已经过 asyncio.to_thread 卸载到线程池)。

_TEI_EMBED_BATCH = 16  # 单次 /embed 请求的文本条数上限(控制 payload 与 TEI 批内存)


class TeiEmbeddingClient:
    """HuggingFace text-embeddings-inference 的 /embed 客户端。"""

    def __init__(self, base_url: str, timeout: float = 60.0, transport=None):
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport,
            verify=settings.network.tls_verify,
        )

    def embed(self, texts: List[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for start in range(0, len(texts), _TEI_EMBED_BATCH):
            batch = texts[start:start + _TEI_EMBED_BATCH]
            response = self._client.post("/embed", json={"inputs": batch, "truncate": True})
            response.raise_for_status()
            vectors.extend(response.json())
        return vectors


class LocalEmbeddingClient:
    """进程内 sentence-transformers 嵌入(需 rag-embedded extra)。"""

    def __init__(self, model_name_or_path: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 依赖缺失的明确报错
            raise RuntimeError(
                "嵌入模式需要 rag-embedded extra(uv sync --extra rag-embedded / "
                "镜像 --build-arg WITH_RAG=1),或改配 [rag] embedding_url 走 TEI 远程推理"
            ) from exc
        self._model = SentenceTransformer(model_name_or_path)

    def embed(self, texts: List[str]) -> List[List[float]]:
        return self._model.encode(list(texts)).tolist()


class TeiRerankClient:
    """TEI 的 /rerank 客户端(bge-reranker 系列)。"""

    def __init__(self, base_url: str, timeout: float = 60.0, transport=None):
        self._client = httpx.Client(
            base_url=base_url, timeout=timeout, transport=transport,
            verify=settings.network.tls_verify,
        )

    def rerank_scores(self, query: str, texts: List[str]) -> List[float]:
        response = self._client.post(
            "/rerank", json={"query": query, "texts": texts, "truncate": True}
        )
        response.raise_for_status()
        scores = [0.0] * len(texts)
        for row in response.json():
            scores[int(row["index"])] = float(row["score"])
        return scores


class LocalRerankClient:
    """进程内 CrossEncoder 重排(需 rag-embedded extra)。"""

    def __init__(self, model_name_or_path: str, logger):
        from sentence_transformers import CrossEncoder
        logger.info(f"🔀 正在加载 Cross-Encoder 重排模型: {model_name_or_path}")
        self._model = CrossEncoder(model_name_or_path)
        logger.info("✅ Cross-Encoder 就绪")

    def rerank_scores(self, query: str, texts: List[str]) -> List[float]:
        return [float(s) for s in self._model.predict([(query, t) for t in texts])]

# ── T8: 文本清洗 ─────────────────────────────────────────────────────────────

# 正文有效的最小字符数；低于此阈值视为"无正文"，走 T6 回退路径
_MIN_BODY_CHARS = 30

# HN 文章的样板行模式（Article URL / Comments URL / Points / # Comments）
_HN_BOILERPLATE_RE = re.compile(
    r'(Article URL:[^\n]*|Comments URL:[^\n]*|Points:\s*\d+[^\n]*|#\s*Comments:\s*\d+[^\n]*)\n?',
    re.IGNORECASE
)
# HN 正文中孤立的 URL 行（去掉标签行后残留的 https://... 单行）
_STANDALONE_URL_RE = re.compile(r'^https?://\S+\s*$', re.MULTILINE)
# arxiv 前缀：如 "arXiv:2605.06671v1 Announce Type: new\n"
_ARXIV_PREFIX_RE = re.compile(r'arXiv:\S+\s+Announce Type:\s+\S+\s*\n?', re.IGNORECASE)
# HTML 标签（仅做基础剥离，不引入 BeautifulSoup 依赖）
_HTML_TAG_RE = re.compile(r'<[^>]+>')
# markdown 图片语法 ![alt](url)
_MD_IMAGE_RE = re.compile(r'!\[[^\]]*\]\([^)]+\)')
# 连续三个以上换行收缩为两个
_MULTI_NEWLINE_RE = re.compile(r'\n{3,}')
# 连续两个以上空格收缩为一个
_MULTI_SPACE_RE = re.compile(r'[ \t]{2,}')


def clean_text(text: str) -> str:
    """
    T8: 对入库正文进行标准化清洗，剔除噪声后再切片/向量化。

    处理顺序：
    0. markdown 图片语法剥离（![alt](url)，避免图片 URL 污染 embedding）
    1. HTML 标签剥离
    2. HN 样板行剥离（Article URL / Comments URL / Points / # Comments）
    3. arxiv 声明前缀剥离（保留 Abstract 正文）
    4. 空白符规范化
    """
    if not text:
        return ""

    # 0. markdown 图片语法（正文现在可能含 ![](url)，URL 对向量是噪声）
    text = _MD_IMAGE_RE.sub('', text)

    # 1. HTML 标签
    if '<' in text and '>' in text:
        text = _HTML_TAG_RE.sub(' ', text)

    # 2. HN 样板（先去标签行，再去残留的孤立 URL 行）
    text = _HN_BOILERPLATE_RE.sub('', text)
    text = _STANDALONE_URL_RE.sub('', text)

    # 3. arxiv 前缀
    text = _ARXIV_PREFIX_RE.sub('', text)
    # 移除残留的 "Abstract: " 前缀（arxiv 摘要开头）
    text = re.sub(r'^Abstract:\s*', '', text.lstrip())

    # 4. 空白规范化
    text = _MULTI_SPACE_RE.sub(' ', text)
    text = _MULTI_NEWLINE_RE.sub('\n\n', text)
    text = text.strip()

    return text


# ── T1: 来源名称映射（提升头部可读性，辅助中文查询匹配）────────────────────────
SOURCE_FRIENDLY_NAMES: Dict[str, str] = {
    "rss_arxiv_cs_ai":           "arXiv cs.AI",
    "rss_arxiv_cs_cl":           "arXiv cs.CL",
    "rss_google_ai_blog":        "Google AI Blog",
    "rss_google_deepmind_news":  "Google DeepMind",
    "rss_hn_ai":                 "Hacker News AI",
    "rss_huggingface_blog":      "HuggingFace Blog",
    "rss_microsoft_ai_blog":     "Microsoft AI Blog",
    "rss_nvidia_developer_blog": "NVIDIA Developer Blog",
    "rss_openai_news":           "OpenAI News",
    "rss_dify_releases":         "Dify Releases",
    "rss_ollama_releases":       "Ollama Releases",
    "rss_vllm_releases":         "vLLM Releases",
    "web_anthropic_news":        "Anthropic News",
    "web_claude_blog":           "Claude Blog (Anthropic)",
    "web_mistral_news":          "Mistral AI News",
    "web_runway_news":           "Runway ML News",
    "dorami_daily_brief":        "哆啦美·AI资讯日报",
}


# ── T2: 文档头部构建 ──────────────────────────────────────────────────────────

def build_document_header(source_id: str, publish_date: str, title: str) -> str:
    """
    为每个 chunk 构建元数据头部。
    将来源名称、日期、标题嵌入 chunk 文本，使时效性/来源类查询可直接命中。
    """
    friendly_name = SOURCE_FRIENDLY_NAMES.get(source_id, source_id)
    date_str = publish_date[:10] if publish_date else ""
    return f"来源: {friendly_name} | 日期: {date_str}\n标题: {title}\n\n"


# ── T2: 段落感知切片 ──────────────────────────────────────────────────────────

def paragraph_chunk_text(text: str, chunk_size: int = 800, overlap: int = 150) -> List[str]:
    """
    按段落边界优先切分文本，段落过长时再按句子边界切分。
    相比纯字符窗口，语义完整性更好，重叠量也更大（150 vs 50）。
    """
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    paragraphs = [p.strip() for p in re.split(r'\n{2,}', text) if p.strip()]
    if not paragraphs:
        paragraphs = [text]

    chunks: List[str] = []
    current = ""

    def _flush(buf: str) -> str:
        """Append buf to chunks, return overlap tail for next chunk."""
        chunks.append(buf)
        return buf[-overlap:] if len(buf) > overlap else buf

    for para in paragraphs:
        if len(para) > chunk_size:
            # 段落本身超长：先 flush 当前缓冲，再按句子切分
            if current:
                current = _flush(current)
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sent in sentences:
                candidate = (current + " " + sent).strip() if current else sent
                if len(candidate) <= chunk_size:
                    current = candidate
                else:
                    if current:
                        current = _flush(current)
                    # 单句仍超长时强制截断
                    while len(sent) > chunk_size:
                        chunks.append(sent[:chunk_size])
                        sent = sent[chunk_size - overlap:]
                    current = sent
        elif len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            current = _flush(current)
            overlap_tail = current  # _flush 已更新 current 为 tail
            current = (overlap_tail + "\n\n" + para).strip() if overlap_tail else para

    if current:
        chunks.append(current)

    return chunks or [text[:chunk_size]]


# ── 存储类 ────────────────────────────────────────────────────────────────────

class ChromaVectorStorage(BaseStorage):
    def __init__(self, db_path: str = None,
                 collection_name: str = "dorami_docs"):
        super().__init__()
        if db_path is None:
            db_path = settings.storage.chroma_path
        # 远程模式(chroma_url 配置)不落本地目录;嵌入模式才建 PersistentClient 目录
        if not settings.rag.remote:
            os.makedirs(db_path, exist_ok=True)
        self._db_path = db_path
        self._collection_name = collection_name
        # chromadb client / 嵌入 provider / collection 均推迟到首次使用时再加载，
        # 避免后端启动时即下载/加载模型权重或连远端服务。
        self._client = None
        self._embedder = None
        self._collection = None

        # T12: reranker provider — lazy-loaded on first use;加载失败(依赖缺失且
        # 未配 rerank_url)时置 False 哨兵,rerank() 优雅跳过而非反复重试。
        self._reranker = None
        self._reranker_model = settings.models.reranker_model

        # 懒加载在线程池中触发（向量操作经 asyncio.to_thread 卸载），
        # 故首次初始化需加锁防止并发重复加载模型/建集合。
        self._init_lock = threading.Lock()

        # Chroma collection 操作级互斥锁：向量操作改用线程池后由「事件循环上隐式串行」
        # 变为「多线程并发」，而 rebuild_collection 会删除并重建 collection——重建若与
        # 并发的 add/query/delete 撞同一 collection 会崩溃/不一致。用一把锁串行化所有
        # collection 访问（锁在工作线程内持有，事件循环仍不被阻塞）。
        # 注意：与 _init_lock 是两把锁，加锁顺序恒为 _op_lock → _init_lock，无死锁。
        self._op_lock = threading.Lock()

    # ── 懒加载 ───────────────────────────────────────────────────────────────
    def _ensure_collection(self):
        if self._collection is None:
            with self._init_lock:
                if self._collection is None:
                    self._load_collection()
        return self._collection

    def _load_collection(self):
        if self._collection is None:
            rag = settings.rag
            if rag.remote:
                parsed = urlparse(rag.chroma_url)
                self.logger.info(f"🧬 连接 Chroma server: {rag.chroma_url} | TEI 嵌入: {rag.embedding_url}")
                self._client = chromadb.HttpClient(
                    host=parsed.hostname or "localhost",
                    port=parsed.port or (443 if parsed.scheme == "https" else 8000),
                    ssl=parsed.scheme == "https",
                )
                if not rag.embedding_url:
                    raise RuntimeError("[rag] chroma_url 已配置但 embedding_url 为空——远程模式需要 TEI 嵌入服务")
                self._embedder = TeiEmbeddingClient(rag.embedding_url)
            else:
                model_name_or_path = settings.models.embedding_model
                self.logger.info(f"🧬 正在加载 Embedding 模型: {model_name_or_path}")
                self._client = chromadb.PersistentClient(path=self._db_path)
                try:
                    self._embedder = LocalEmbeddingClient(model_name_or_path)
                except Exception as e:
                    self.logger.error(f"❌ 向量模型加载失败 [{model_name_or_path}]: {e}")
                    raise
            # 不再挂 embedding_function——向量一律由 provider 显式计算后传入,
            # add/query 两侧共用同一嵌入来源,嵌入/远程模式行为一致。
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self.logger.info(f"🗂️ 向量特征库就绪: 集合 {self._collection_name}")
        return self._collection

    @property
    def client(self):
        self._ensure_collection()
        return self._client

    @property
    def collection(self):
        return self._ensure_collection()

    @property
    def embedder(self):
        self._ensure_collection()
        return self._embedder

    def _locked(self, fn, *args, **kwargs):
        """在 collection 操作级互斥锁下执行 fn（在工作线程内调用）。

        把单个 collection 操作（save 内的 get+delete+add 视为整体）串行化，
        避免与 rebuild_collection 的删除/重建并发竞争。
        """
        with self._op_lock:
            return fn(*args, **kwargs)

    # ── 写入 ──────────────────────────────────────────────────────────────────

    async def save(self, item: BaseContent) -> bool:
        """将文章向量化并写入 ChromaDB（embedding 为 CPU 重操作，经线程池卸载）。"""
        return await asyncio.to_thread(self._locked, self._save_blocking, item)

    def _save_blocking(self, item: BaseContent) -> bool:
        """
        将文章向量化并写入 ChromaDB。

        T2: 每个 chunk 前置元数据头部（来源名/日期/标题）。
        T6: 无正文内容时以头部单 chunk 建立索引，保证空正文文章也可被检索到。
        """
        header = build_document_header(item.source_id, item.publish_date, item.title)
        # T8: 清洗后再判断是否有有效正文
        body = clean_text(item.content or "")

        # 幂等检查；如果旧版本只是 header-only，而本次已有正文，则重建 chunks。
        existing = self.collection.get(where={"parent_id": item.id})
        if existing and existing["ids"]:
            existing_has_body = any(
                bool(metadata.get("has_body"))
                for metadata in (existing.get("metadatas") or [])
                if metadata
            )
            if existing_has_body or len(body) < _MIN_BODY_CHARS:
                return False
            self.collection.delete(where={"parent_id": item.id})

        # T6: 有效正文不足 _MIN_BODY_CHARS 字符时，以头部单 chunk 建立索引
        if len(body) < _MIN_BODY_CHARS:
            full_chunks = [header.rstrip()]
        else:
            body_chunks = paragraph_chunk_text(body)
            full_chunks = [header + chunk for chunk in body_chunks]

        ids = [f"{item.id}_chunk_{i}" for i in range(len(full_chunks))]
        metadatas = [
            {
                "parent_id": item.id,
                "title": item.title,
                "content_type": item.content_type,
                "source_id": item.source_id,
                "publish_date": item.publish_date[:10] if item.publish_date else "",
                "chunk_index": i,
                "total_chunks": len(full_chunks),
                "has_body": bool(body),
            }
            for i in range(len(full_chunks))
        ]

        try:
            self.collection.add(
                ids=ids,
                documents=full_chunks,
                metadatas=metadatas,
                embeddings=self.embedder.embed(full_chunks),
            )
            body_note = f"{len(full_chunks)} chunks" if body else "header-only"
            self.logger.info(f"✅ [{item.title[:50]}] → {body_note}")
            return True
        except Exception as e:
            self.logger.error(f"向量化写入失败 [{item.id}]: {e}")
            return False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def get(self, id: str) -> Optional[Dict[str, Any]]:
        return await asyncio.to_thread(self._locked, self._get_blocking, id)

    def _get_blocking(self, id: str) -> Optional[Dict[str, Any]]:
        res = self.collection.get(where={"parent_id": id})
        if res and res["ids"]:
            return {"chunks_count": len(res["ids"]), "parent_id": id}
        return None

    async def update(self, id: str, data: dict) -> bool:
        # 更新策略：先删后增，由调用方负责重新调用 save()
        return False

    async def delete(self, id: str) -> bool:
        return await asyncio.to_thread(self._locked, self._delete_blocking, id)

    def _delete_blocking(self, id: str) -> bool:
        try:
            self.collection.delete(where={"parent_id": id})
            self.logger.info(f"🗑️ 已删除文章 [{id}] 的所有向量 chunk")
            return True
        except Exception as e:
            self.logger.error(f"删除向量 chunk 失败 [{id}]: {e}")
            return False

    # ── 检索 ──────────────────────────────────────────────────────────────────

    async def search(self, query: str, n_results: int = 5,
                     content_type: str = None, source_id: str = None,
                     source_ids: Optional[List[str]] = None,
                     publish_date_gte: str = None,
                     publish_date_lte: str = None,
                     days_ago: int = None) -> List[Dict[str, Any]]:
        """语义检索（query 端 embedding 为 CPU 重操作，经线程池卸载）。"""
        return await asyncio.to_thread(
            self._locked, self._search_blocking,
            query, n_results, content_type, source_id,
            source_ids, publish_date_gte, publish_date_lte, days_ago,
        )

    def _search_blocking(self, query: str, n_results: int = 5,
                         content_type: str = None, source_id: str = None,
                         source_ids: Optional[List[str]] = None,
                         publish_date_gte: str = None,
                         publish_date_lte: str = None,
                         days_ago: int = None) -> List[Dict[str, Any]]:
        """
        语义检索，支持 content_type / source_id / source_ids / publish_date 元数据过滤。
        source_ids 为来源白名单（如某用户订阅的源集合），与 source_id 取并集约束。
        日期格式: 'YYYY-MM-DD'（利用 ISO 日期字符串的字典序比较性质）。
        """
        conditions = []
        if content_type:
            conditions.append({"content_type": content_type})
        if source_id:
            conditions.append({"source_id": source_id})
        if source_ids is not None:
            unique_source_ids = [sid for sid in dict.fromkeys(source_ids) if sid]
            if len(unique_source_ids) == 1:
                conditions.append({"source_id": unique_source_ids[0]})
            elif len(unique_source_ids) > 1:
                conditions.append({"source_id": {"$in": unique_source_ids}})
            else:
                conditions.append({"source_id": "__none__"})
        if publish_date_gte:
            conditions.append({"publish_date": {"$gte": publish_date_gte}})
        if publish_date_lte:
            conditions.append({"publish_date": {"$lte": publish_date_lte}})

        where = None
        if len(conditions) == 1:
            where = conditions[0]
        elif len(conditions) > 1:
            where = {"$and": conditions}

        results = self.collection.query(
            query_embeddings=self.embedder.embed([query]),
            n_results=n_results,
            where=where,
        )

        formatted = []
        if results and results["ids"] and results["ids"][0]:
            for i in range(len(results["ids"][0])):
                formatted.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] if "distances" in results else 0.0,
                })
        return formatted

    # ── 统计与管理 ────────────────────────────────────────────────────────────

    async def count(self) -> int:
        return await asyncio.to_thread(self._locked, lambda: self.collection.count())

    async def list_parent_ids(self) -> set:
        """返回集合中实际存在的全部去重 parent_id（供 SQLite↔Chroma 对账）。

        一篇文章在 Chroma 中是若干 chunk，每个 chunk 的 metadata 带同一 parent_id
        （= ArticleRecord.id）。这里只取 metadatas 里的 parent_id 去重，不拉 documents/
        embeddings，尽量轻量。"""
        return await asyncio.to_thread(self._locked, self._list_parent_ids_blocking)

    def _list_parent_ids_blocking(self) -> set:
        res = self.collection.get(include=["metadatas"])
        return {
            metadata["parent_id"]
            for metadata in (res.get("metadatas") or [])
            if metadata and metadata.get("parent_id")
        }

    # ── T12: Cross-encoder 重排序 ─────────────────────────────────────────────

    def _ensure_reranker(self):
        """Lazy-load rerank provider on first call.

        远程:配置了 [rag] rerank_url → TEI /rerank;
        本地:CrossEncoder(需 rag-embedded extra)。
        二者皆不可得(远程模式未配 rerank_url,或本地依赖缺失)→ 置 False 哨兵,
        rerank() 从此优雅跳过——重排本就是可选的精排增强,不该让检索整体失败。
        """
        if self._reranker is None:
            with self._init_lock:
                if self._reranker is None:
                    rag = settings.rag
                    if rag.rerank_url:
                        self._reranker = TeiRerankClient(rag.rerank_url)
                    elif rag.remote:
                        self.logger.info("ℹ️ 远程模式未配置 rerank_url,跳过重排")
                        self._reranker = False
                    else:
                        try:
                            self._reranker = LocalRerankClient(self._reranker_model, self.logger)
                        except ImportError:
                            self.logger.warning("⚠️ 未安装 rag-embedded extra 且未配 rerank_url,跳过重排")
                            self._reranker = False
        return self._reranker

    async def rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        T12: 对 bi-encoder 候选结果重打分，提升精确率。
        返回列表已按 rerank_score 降序排列；原 distance 字段保留供前端显示。
        推理/HTTP 为阻塞重操作，经线程池卸载，避免冻结事件循环。
        无可用重排 provider 时原样返回(可选精排,不阻断检索)。
        """
        if not candidates:
            return candidates
        return await asyncio.to_thread(self._rerank_blocking, query, candidates)

    def _rerank_blocking(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        reranker = self._ensure_reranker()
        if not reranker:
            return candidates
        scores = reranker.rerank_scores(query, [c["document"] for c in candidates])
        for i, c in enumerate(candidates):
            c["rerank_score"] = scores[i]
        return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)

    # ── T13: 相邻 chunk 上下文扩展 ────────────────────────────────────────────

    async def expand_chunk(self, parent_id: str, chunk_index: int,
                           total_chunks: int) -> Dict[str, Optional[str]]:
        return await asyncio.to_thread(
            self._locked, self._expand_chunk_blocking, parent_id, chunk_index, total_chunks
        )

    def _expand_chunk_blocking(self, parent_id: str, chunk_index: int,
                               total_chunks: int) -> Dict[str, Optional[str]]:
        """
        T13: 获取命中 chunk 的前后相邻 chunk 正文，用于上下文窗口扩展。
        返回 {"prev": str | None, "next": str | None}，已去除元数据头部。
        """
        result: Dict[str, Optional[str]] = {"prev": None, "next": None}
        for delta, key in [(-1, "prev"), (1, "next")]:
            target_idx = chunk_index + delta
            if 0 <= target_idx < total_chunks:
                chunk_id = f"{parent_id}_chunk_{target_idx}"
                try:
                    res = self.collection.get(ids=[chunk_id])
                    if res and res["documents"]:
                        doc = res["documents"][0]
                        body_start = doc.find("\n\n")
                        result[key] = doc[body_start + 2:].strip() if body_start != -1 else doc
                except Exception:
                    pass
        return result

    def rebuild_collection(self) -> None:
        """
        删除并重建 ChromaDB collection。
        换用新 embedding 模型后必须调用此方法，否则旧维度向量与新模型不兼容。
        调用后需重新向量化所有文章（POST /api/vector/reindex-all）。

        在操作级锁内执行删除+重建，确保不与并发的 add/query/delete 撞同一 collection。
        """
        with self._op_lock:
            # 强制初始化 client / 嵌入 provider，再删除并重建集合。
            self._ensure_collection()
            try:
                self._client.delete_collection(self._collection_name)
            except Exception:
                pass
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            self.logger.info(f"🔄 向量集合已重建: {self._collection_name}")
