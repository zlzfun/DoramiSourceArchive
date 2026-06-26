import json
import os
from typing import Optional, Dict, Any, Iterable
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

        if "reader_subscriptions" in inspector.get_table_names():
            subscription_columns = {column["name"] for column in inspector.get_columns("reader_subscriptions")}
            if "owner_username" not in subscription_columns:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE reader_subscriptions ADD COLUMN owner_username VARCHAR DEFAULT ''"))

        if "users" in inspector.get_table_names():
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "avatar" not in user_columns:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN avatar VARCHAR"))
            if "ai_beta_enabled" not in user_columns:
                with self.engine.begin() as conn:
                    conn.execute(text("ALTER TABLE users ADD COLUMN ai_beta_enabled BOOLEAN DEFAULT 0"))

        if "source_configs" in inspector.get_table_names():
            source_config_columns = {column["name"] for column in inspector.get_columns("source_configs")}
            source_config_additive_columns = {
                "source_owner": "VARCHAR DEFAULT ''",
                "source_brand": "VARCHAR DEFAULT ''",
                "source_scope": "VARCHAR DEFAULT ''",
                "source_channel": "VARCHAR DEFAULT ''",
                "base_url": "VARCHAR DEFAULT ''",
                "provenance_tier": "VARCHAR DEFAULT ''",
                "content_tags_json": "VARCHAR DEFAULT '[]'",
                "signal_strength": "VARCHAR DEFAULT ''",
                "noise_risk": "VARCHAR DEFAULT ''",
                "fetch_reliability": "VARCHAR DEFAULT ''",
            }
            with self.engine.begin() as conn:
                for column_name, column_sql in source_config_additive_columns.items():
                    if column_name not in source_config_columns:
                        conn.execute(text(f"ALTER TABLE source_configs ADD COLUMN {column_name} {column_sql}"))

    async def save(self, item: BaseContent) -> bool:
        with Session(self.engine) as session:
            existing = session.get(ArticleRecord, item.id)
            if existing:
                if not existing.has_content and item.has_content and item.content:
                    raw_metadata = serialize_to_metadata(item)
                    existing.title = item.title
                    existing.source_url = item.source_url
                    existing.publish_date = item.publish_date
                    existing.fetched_date = item.fetched_date
                    existing.has_content = True
                    existing.content = item.content
                    existing.extensions_json = json.dumps(raw_metadata.get("extensions", {}), ensure_ascii=False)
                    existing.is_vectorized = False
                    session.add(existing)
                    session.commit()
                    return True
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

    async def existing_content_flags(self, item_ids: Iterable[str]) -> Dict[str, bool]:
        """批量查询给定 id 是否已入库及是否已有正文。

        返回 ``{id: has_content}``，仅包含库中已存在的 id（缺席即代表全新条目）。
        仅取主键与 has_content 两列，供抓取器在请求正文前做去重预检，
        避免对重复条目重复访问正文 URL（详见 fetcher 的去重钩子）。
        """
        ids = [item_id for item_id in dict.fromkeys(item_ids) if item_id]
        if not ids:
            return {}
        flags: Dict[str, bool] = {}
        with Session(self.engine) as session:
            statement = select(ArticleRecord.id, ArticleRecord.has_content).where(
                ArticleRecord.id.in_(ids)
            )
            for row_id, has_content in session.exec(statement).all():
                flags[row_id] = bool(has_content)
        return flags

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
