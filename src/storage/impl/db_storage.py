import json
import os
from typing import Optional, Dict, Any
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine, select
from storage.base import BaseStorage
from models.content import BaseContent, serialize_to_metadata
from models.db import ArticleRecord, SQLModel


class DatabaseStorage(BaseStorage):
    def __init__(self, db_url: str = "sqlite:///./data/cms_data.db"):
        super().__init__()

        db_path = db_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir: os.makedirs(db_dir, exist_ok=True)

        self.engine = create_engine(db_url, echo=False)
        SQLModel.metadata.create_all(self.engine)
        self._ensure_compatible_schema()
        self.logger.info(f"🗄️ 关系型数据库已连接: {db_url}")

    def _ensure_compatible_schema(self):
        """Lightweight SQLite-compatible migrations for additive schema changes."""
        inspector = inspect(self.engine)
        if "fetch_runs" not in inspector.get_table_names():
            return

        existing_columns = {column["name"] for column in inspector.get_columns("fetch_runs")}
        additive_columns = {
            "job_id": "INTEGER",
            "job_run_id": "INTEGER",
            "source_group_id": "INTEGER",
            "run_scope": "VARCHAR DEFAULT 'ad_hoc'",
        }

        with self.engine.begin() as conn:
            for column_name, column_sql in additive_columns.items():
                if column_name not in existing_columns:
                    conn.execute(text(f"ALTER TABLE fetch_runs ADD COLUMN {column_name} {column_sql}"))

        if "articles" not in inspector.get_table_names():
            return

        article_columns = {column["name"] for column in inspector.get_columns("articles")}
        article_additive_columns = {
            "fetch_run_id": "INTEGER",
            "job_id": "INTEGER",
            "job_run_id": "INTEGER",
            "source_group_id": "INTEGER",
            "run_scope": "VARCHAR DEFAULT 'ad_hoc'",
        }
        with self.engine.begin() as conn:
            for column_name, column_sql in article_additive_columns.items():
                if column_name not in article_columns:
                    conn.execute(text(f"ALTER TABLE articles ADD COLUMN {column_name} {column_sql}"))

        if "node_groups" in inspector.get_table_names():
            node_group_columns = {column["name"] for column in inspector.get_columns("node_groups")}
            node_group_additive_columns = {
                "params_json": "VARCHAR DEFAULT '{}'",
                "per_fetcher_params_json": "VARCHAR DEFAULT '{}'",
                "cron_expr": "VARCHAR DEFAULT ''",
                "per_fetcher_cron_json": "VARCHAR DEFAULT '{}'",
            }
            with self.engine.begin() as conn:
                for column_name, column_sql in node_group_additive_columns.items():
                    if column_name not in node_group_columns:
                        conn.execute(text(f"ALTER TABLE node_groups ADD COLUMN {column_name} {column_sql}"))

        if "collection_jobs" in inspector.get_table_names():
            collection_job_columns = {column["name"] for column in inspector.get_columns("collection_jobs")}
            if "per_fetcher_cron_json" not in collection_job_columns:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE collection_jobs ADD COLUMN per_fetcher_cron_json VARCHAR DEFAULT '{}'"))

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
                fetch_run_id=getattr(item, "fetch_run_id", None),
                job_id=getattr(item, "job_id", None),
                job_run_id=getattr(item, "job_run_id", None),
                source_group_id=getattr(item, "source_group_id", None),
                run_scope=getattr(item, "run_scope", "ad_hoc"),
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
