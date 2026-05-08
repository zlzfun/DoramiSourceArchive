import json
import os
from typing import Optional, Dict, Any
from sqlmodel import Session, create_engine, select
from storage.base import BaseStorage
from models.content import BaseContent, serialize_to_metadata
from models.db import ArticleRecord, FetchTaskRecord, SQLModel


class DatabaseStorage(BaseStorage):
    def __init__(self, db_url: str = "sqlite:///./data/cms_data.db"):
        super().__init__()

        db_path = db_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir: os.makedirs(db_dir, exist_ok=True)

        self.engine = create_engine(db_url, echo=False)
        SQLModel.metadata.create_all(self.engine)
        self.logger.info(f"🗄️ 关系型数据库已连接: {db_url}")

    async def save(self, item: BaseContent) -> bool:
        with Session(self.engine) as session:
            existing = session.get(ArticleRecord, item.id)
            if existing:
                return False

            raw_metadata = serialize_to_metadata(item)
            extensions = raw_metadata.get("extensions", {})
            actual_content = item.content if item.content else extensions.get("summary")

            record = ArticleRecord(
                id=item.id,
                title=item.title,
                # 【架构重构】: 将原先模糊的 source_type 替换为确切的结构类别与来源标识
                content_type=item.content_type,
                source_id=item.source_id,
                source_url=item.source_url,
                publish_date=item.publish_date,
                fetched_date=item.fetched_date,
                has_content=item.has_content,
                content=actual_content,
                extensions_json=json.dumps(extensions, ensure_ascii=False),
                is_vectorized=False
            )
            session.add(record)
            session.commit()
            return True

    # --- 统一标准的 CRUD 操作 ---

    async def get(self, item_id: str) -> Optional[ArticleRecord]:
        with Session(self.engine) as session:
            return session.get(ArticleRecord, item_id)

    async def update(self, item_id: str, updates: Dict[str, Any]) -> bool:
        with Session(self.engine) as session:
            record = session.get(ArticleRecord, item_id)
            if not record:
                return False
            for key, value in updates.items():
                if hasattr(record, key):
                    setattr(record, key, value)
            session.commit()
            return True

    async def delete(self, item_id: str) -> bool:
        with Session(self.engine) as session:
            record = session.get(ArticleRecord, item_id)
            if not record:
                return False
            session.delete(record)
            session.commit()
            return True

    # --- 业务特化方法 (依然基于标准 CRUD) ---

    async def mark_as_vectorized(self, article_id: str) -> bool:
        """标记文章已完成向量化"""
        return await self.update(article_id, {"is_vectorized": True})

    async def mark_as_unvectorized(self, article_id: str) -> bool:
        """重置文章的向量化状态"""
        return await self.update(article_id, {"is_vectorized": False})