"""
ChromaDB 向量存储实现 (src/storage/impl/vector_storage.py)

T1: 默认模型升级为 BAAI/bge-m3（多语言，支持中文查询检索英文文档）
T2: 每个 chunk 前置元数据头部（来源/日期/标题），支持时效性与来源类查询
T6: 空正文内容回退——无正文文章仍可通过标题+头部建立索引，不再被跳过
T8: 入库前文本清洗（HTML 剥离 / HN 样板去除 / arxiv 前缀剥离 / 短无效内容剔除）
"""

import os
import re
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.utils import embedding_functions
from storage.base import BaseStorage
from models.content import BaseContent

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
# 连续三个以上换行收缩为两个
_MULTI_NEWLINE_RE = re.compile(r'\n{3,}')
# 连续两个以上空格收缩为一个
_MULTI_SPACE_RE = re.compile(r'[ \t]{2,}')


def clean_text(text: str) -> str:
    """
    T8: 对入库正文进行标准化清洗，剔除噪声后再切片/向量化。

    处理顺序：
    1. HTML 标签剥离
    2. HN 样板行剥离（Article URL / Comments URL / Points / # Comments）
    3. arxiv 声明前缀剥离（保留 Abstract 正文）
    4. 空白符规范化
    """
    if not text:
        return ""

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
    def __init__(self, db_path: str = "./data/chroma_db",
                 collection_name: str = "dorami_docs"):
        super().__init__()
        self._collection_name = collection_name
        os.makedirs(db_path, exist_ok=True)
        self.client = chromadb.PersistentClient(path=db_path)

        # T1: 默认换用 BAAI/bge-m3（多语言，支持中文查询→英文文档跨语言检索）
        # 可通过 LOCAL_MODEL_PATH 环境变量指向本地私有化路径
        default_model = "BAAI/bge-m3"
        model_name_or_path = os.environ.get("LOCAL_MODEL_PATH", default_model)
        self.logger.info(f"🧬 正在加载 Embedding 模型: {model_name_or_path}")

        try:
            self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=model_name_or_path
            )
        except Exception as e:
            self.logger.error(f"❌ 向量模型加载失败 [{model_name_or_path}]: {e}")
            raise

        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self.logger.info(f"🗂️ 向量特征库就绪: {db_path} | 集合: {collection_name}")

        # T12: Cross-encoder reranker — lazy-loaded on first use
        self._reranker = None
        self._reranker_model = os.environ.get("RERANKER_MODEL_PATH", "BAAI/bge-reranker-v2-m3")

    # ── 写入 ──────────────────────────────────────────────────────────────────

    async def save(self, item: BaseContent) -> bool:
        """
        将文章向量化并写入 ChromaDB。

        T2: 每个 chunk 前置元数据头部（来源名/日期/标题）。
        T6: 无正文内容时以头部单 chunk 建立索引，保证空正文文章也可被检索到。
        """
        # 幂等检查
        existing = self.collection.get(where={"parent_id": item.id})
        if existing and existing["ids"]:
            return False

        header = build_document_header(item.source_id, item.publish_date, item.title)
        # T8: 清洗后再判断是否有有效正文
        body = clean_text(item.content or "")

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
            self.collection.add(ids=ids, documents=full_chunks, metadatas=metadatas)
            body_note = f"{len(full_chunks)} chunks" if body else "header-only"
            self.logger.info(f"✅ [{item.title[:50]}] → {body_note}")
            return True
        except Exception as e:
            self.logger.error(f"向量化写入失败 [{item.id}]: {e}")
            return False

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def get(self, id: str) -> Optional[Dict[str, Any]]:
        res = self.collection.get(where={"parent_id": id})
        if res and res["ids"]:
            return {"chunks_count": len(res["ids"]), "parent_id": id}
        return None

    async def update(self, id: str, data: dict) -> bool:
        # 更新策略：先删后增，由调用方负责重新调用 save()
        return False

    async def delete(self, id: str) -> bool:
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
                     publish_date_gte: str = None,
                     publish_date_lte: str = None,
                     days_ago: int = None) -> List[Dict[str, Any]]:
        """
        语义检索，支持 content_type / source_id / publish_date 四维元数据过滤。
        日期格式: 'YYYY-MM-DD'（利用 ISO 日期字符串的字典序比较性质）。
        """
        conditions = []
        if content_type:
            conditions.append({"content_type": content_type})
        if source_id:
            conditions.append({"source_id": source_id})
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
            query_texts=[query],
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
        return self.collection.count()

    # ── T12: Cross-encoder 重排序 ─────────────────────────────────────────────

    def _ensure_reranker(self):
        """Lazy-load cross-encoder model on first rerank call."""
        if self._reranker is None:
            from sentence_transformers import CrossEncoder
            self.logger.info(f"🔀 正在加载 Cross-Encoder 重排模型: {self._reranker_model}")
            self._reranker = CrossEncoder(self._reranker_model)
            self.logger.info(f"✅ Cross-Encoder 就绪")
        return self._reranker

    def rerank(self, query: str, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        T12: 用 cross-encoder 对 bi-encoder 候选结果重打分，提升精确率。
        返回列表已按 rerank_score 降序排列；原 distance 字段保留供前端显示。
        """
        if not candidates:
            return candidates
        reranker = self._ensure_reranker()
        pairs = [(query, c["document"]) for c in candidates]
        scores = reranker.predict(pairs)
        for i, c in enumerate(candidates):
            c["rerank_score"] = float(scores[i])
        return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)

    # ── T13: 相邻 chunk 上下文扩展 ────────────────────────────────────────────

    async def expand_chunk(self, parent_id: str, chunk_index: int,
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
        """
        try:
            self.client.delete_collection(self._collection_name)
        except Exception:
            pass
        self.collection = self.client.get_or_create_collection(
            name=self._collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )
        self.logger.info(f"🔄 向量集合已重建: {self._collection_name}")
