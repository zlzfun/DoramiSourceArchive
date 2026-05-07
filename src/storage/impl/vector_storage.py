"""
ChromaDB 向量存储实现 (src/storage/impl/vector_storage.py)

负责将文章内容进行文本切片 (Chunking)，提取向量特征，并存入 ChromaDB。
支持从环境变量 LOCAL_MODEL_PATH 动态加载本地私有化 Embedding 模型。
"""

import os
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.utils import embedding_functions
from storage.base import BaseStorage
from models.content import BaseContent


# 一个极其简单的文本滑动窗口切片器
def simple_chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    if not text:
        return []
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(text[start:end])
        if end == text_len:
            break
        start += chunk_size - overlap
    return chunks


class ChromaVectorStorage(BaseStorage):
    def __init__(self, db_path: str = "./data/chroma_db", collection_name: str = "dorami_docs"):
        super().__init__()

        # 1. 确保数据库目录存在
        os.makedirs(db_path, exist_ok=True)

        # 2. 初始化 Chroma 持久化客户端
        self.client = chromadb.PersistentClient(path=db_path)

        # ✨ 修复与优化: 从环境变量动态获取本地模型路径 (支持离线部署)
        # 如果未配置 LOCAL_MODEL_PATH，则默认使用 huggingface 的线上通用小模型
        default_model = "sentence-transformers/all-MiniLM-L6-v2"
        model_name_or_path = os.environ.get("LOCAL_MODEL_PATH", default_model)

        self.logger.info(f"🧬 正在加载向量 Embedding 模型: {model_name_or_path}")

        try:
            self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=model_name_or_path
            )
        except Exception as e:
            self.logger.error(f"❌ 向量模型加载失败，请检查路径 [{model_name_or_path}] 或网络连接。错误: {e}")
            raise e

        # 3. 获取或创建集合 (Collection)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
        )
        self.logger.info(f"🗂️ 向量特征库已就绪: {db_path} | 集合: {collection_name}")

    async def save(self, item: BaseContent) -> bool:
        """
        保存内容到向量库：
        由于一篇文章可能很长（如论文），为了保证大模型召回精度，
        这里对长文本进行分块 (Chunking)，每个 Chunk 作为一个独立的向量存入，但绑定相同的父级 metadata。
        """
        # 如果没有正文内容，就不存入向量库
        if not item.has_content or not item.content:
            return False

        # 检查是否已经存在（通过查询父级 ID）
        existing = self.collection.get(where={"parent_id": item.id})
        if existing and existing['ids']:
            return False  # 已经向量化过了

        # 1. 文本切片
        # 这里把标题和正文拼起来切片，保证切片带有全局上下文倾向
        full_text = f"【{item.title}】\n{item.content}"
        chunks = simple_chunk_text(full_text)

        if not chunks:
            return False

        # 2. 准备写入 ChromaDB 的数据结构
        ids = []
        documents = []
        metadatas = []

        for i, chunk_text in enumerate(chunks):
            # 为每个切片生成唯一的 ID，例如: huggingface_daily_001_chunk_0
            chunk_id = f"{item.id}_chunk_{i}"
            ids.append(chunk_id)
            documents.append(chunk_text)

            # 继承双维度高价值元数据，用于后续的极速精准过滤
            metadatas.append({
                "parent_id": item.id,
                "title": item.title,
                "content_type": item.content_type,
                "source_id": item.source_id,
                "publish_date": item.publish_date,
                "chunk_index": i
            })

        # 3. 批量写入
        try:
            self.collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            self.logger.info(f"✅ 成功将文章 [{item.title}] 拆分为 {len(chunks)} 个向量切片入库。")
            return True
        except Exception as e:
            self.logger.error(f"写入向量库失败 [{item.id}]: {e}")
            return False

    async def get(self, id: str) -> Optional[Dict[str, Any]]:
        """在向量库中，通常不通过 get 获取原文，这里为了实现 BaseStorage 接口仅简单返回"""
        res = self.collection.get(where={"parent_id": id})
        if res and res['ids']:
            return {"chunks_count": len(res['ids']), "parent_id": id}
        return None

    async def update(self, id: str, data: dict) -> bool:
        """向量库的更新逻辑极其复杂（涉及重切片），一般采用先删后增的策略。此处暂不支持直接update。"""
        return False

    async def delete(self, id: str) -> bool:
        """删除该文章下的所有切片"""
        try:
            self.collection.delete(where={"parent_id": id})
            self.logger.info(f"🗑️ 已抹除文章 [{id}] 的所有向量特征。")
            return True
        except Exception as e:
            self.logger.error(f"删除向量特征失败 [{id}]: {e}")
            return False

    async def search(self, query: str, n_results: int = 5, content_type: str = None, source_id: str = None,
                     days_ago: int = None) -> List[Dict[str, Any]]:
        """
        语义检索引擎
        支持自然语言 query 与元数据的 $and 混合过滤！
        """
        # 构建 Chroma 的 $and 过滤字典
        where_filters = {}
        conditions = []

        if content_type:
            conditions.append({"content_type": content_type})
        if source_id:
            conditions.append({"source_id": source_id})

        # 注意: ChromaDB 对于日期的 >= 过滤比较有限，如果需要严格的时间过滤，
        # 通常建议将日期转为时间戳存为 number metadata。
        # 这里演示基本的精准匹配逻辑

        if len(conditions) == 1:
            where_filters = conditions[0]
        elif len(conditions) > 1:
            where_filters = {"$and": conditions}

        # 执行召回
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where_filters if where_filters else None
        )

        # 格式化返回值
        formatted_results = []
        if results and results['ids'] and len(results['ids'][0]) > 0:
            for i in range(len(results['ids'][0])):
                formatted_results.append({
                    "id": results['ids'][0][i],
                    "document": results['documents'][0][i],
                    "metadata": results['metadatas'][0][i],
                    "distance": results['distances'][0][i] if 'distances' in results else 0.0
                })
        return formatted_results

    async def count(self) -> int:
        return self.collection.count()