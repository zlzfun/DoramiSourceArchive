import json
import os
from typing import Optional, Dict, Any, Iterable
from sqlalchemy import inspect, text, event
from sqlmodel import Session, create_engine, select
from storage.base import BaseStorage
from models.content import BaseContent, serialize_to_metadata
from models.db import (
    ArticleRecord,
    SQLModel,
)
from services.podcast_metadata import merge_podcast_publisher_metadata


_PLACEHOLDER_ARTICLE_TITLES = {
    "",
    "未命名网页条目",
    "read more",
    "learn more",
    "más información",
}

def _is_placeholder_article_title(value: str) -> bool:
    return (value or "").strip().casefold() in _PLACEHOLDER_ARTICLE_TITLES


class DatabaseStorage(BaseStorage):
    def __init__(self, db_url: str = "sqlite:///./data/cms_data.db"):
        super().__init__()

        db_path = db_url.replace("sqlite:///", "")
        db_dir = os.path.dirname(db_path)
        if db_dir: os.makedirs(db_dir, exist_ok=True)

        is_sqlite = db_url.startswith("sqlite")
        # SQLite：允许跨线程复用连接（asyncio.to_thread / APScheduler 线程池下会用到），
        # 并在每个新连接上启用 WAL（读不阻塞写）+ busy_timeout（写竞争时自动等待而非立即报错）。
        connect_args = {"check_same_thread": False} if is_sqlite else {}
        self.engine = create_engine(db_url, echo=False, connect_args=connect_args)
        if is_sqlite:
            self._enable_sqlite_pragmas(
                self.engine,
                enable_wal=db_url != "sqlite:///:memory:",
            )
        SQLModel.metadata.create_all(self.engine)
        self._ensure_compatible_schema()
        if is_sqlite:
            # FTS5 全文搜索索引（标题+正文）。与 Alembic 迁移共享同一 ensure_fts；
            # 老 SQLite 无 trigram 时内部吞异常降级为标题 LIKE，不影响启动。
            from storage.fts import ensure_fts
            ensure_fts(self.engine)
        self.logger.info(f"🗄️ 关系型数据库已连接: {db_url}")

    @staticmethod
    def _enable_sqlite_pragmas(engine, *, enable_wal: bool) -> None:
        """在每个新建的 SQLite 连接上启用外键、WAL 与 busy_timeout。

        WAL 让读写并发互不阻塞（默认 rollback journal 下读写互斥，并发写极易报
        "database is locked"）；busy_timeout 让写竞争时自动等待 5s 再放弃。
        foreign_keys 默认在 SQLite 每条连接上关闭，必须逐连接开启，才能落实分析、
        taxonomy 和个人日报表声明的 CASCADE/SET NULL 删除语义。内存库同样开启外键，
        只跳过无意义的 WAL。
        """
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _connection_record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                if enable_wal:
                    cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA busy_timeout=5000")
            finally:
                cursor.close()

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
            "archive_updated_at": "VARCHAR NOT NULL DEFAULT ''",
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
            conn.execute(text(
                "UPDATE articles SET archive_updated_at = fetched_date "
                "WHERE archive_updated_at IS NULL OR archive_updated_at = ''"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_articles_archive_updated_at "
                "ON articles (archive_updated_at)"
            ))

        # node_groups 的遗留 additive 迁移已随节点组退役移除（实体简化阶段 2）；
        # 存量表由 Alembic 迁移内联进采集任务后 DROP。

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
            user_additive_columns = {
                "last_login_at": "VARCHAR",
                "ai_translate_count": "INTEGER DEFAULT 0",
                "ai_ask_count": "INTEGER DEFAULT 0",
                "ai_last_used_at": "VARCHAR",
            }
            with self.engine.begin() as conn:
                for column_name, column_sql in user_additive_columns.items():
                    if column_name not in user_columns:
                        conn.execute(text(f"ALTER TABLE users ADD COLUMN {column_name} {column_sql}"))

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
                if (
                    existing.content_type == "podcast_episode"
                    and getattr(item, "content_type", "") == "podcast_episode"
                ):
                    raw_metadata = serialize_to_metadata(item)
                    if not merge_podcast_publisher_metadata(
                        existing,
                        item,
                        raw_metadata.get("extensions", {}),
                    ):
                        return False
                    existing.archive_updated_at = item.fetched_date or existing.archive_updated_at
                    session.add(existing)
                    session.commit()
                    # ``save`` is the pipeline's insertion signal.  The refresh was
                    # persisted, but must not inflate saved_count/saved_content_ids.
                    return False
                if not existing.has_content and item.has_content and item.content:
                    raw_metadata = serialize_to_metadata(item)
                    existing.title = item.title
                    existing.source_url = item.source_url
                    existing.publish_date = item.publish_date
                    existing.fetched_date = item.fetched_date
                    existing.archive_updated_at = item.fetched_date
                    existing.has_content = True
                    existing.content = item.content
                    existing.extensions_json = json.dumps(raw_metadata.get("extensions", {}), ensure_ascii=False)
                    session.add(existing)
                    session.commit()
                    return True
                # 两条极窄的元数据自愈路径：① 旧标题明确是 CTA/占位文案；
                # ② 抓取器显式声明官方元数据权威。普通源默认仍幂等跳过，
                # 绝不因上游改标题而改写忠实归档；两路都不覆盖正文。
                placeholder_repair = (
                    _is_placeholder_article_title(existing.title)
                    and not _is_placeholder_article_title(item.title)
                )
                authoritative_refresh = bool(
                    getattr(item, "_refresh_existing_metadata", False)
                )
                title_changed = bool(item.title) and existing.title != item.title
                publish_date_changed = (
                    bool(item.publish_date)
                    and existing.publish_date != item.publish_date
                )
                source_url_changed = (
                    bool(item.source_url)
                    and existing.source_url != item.source_url
                )
                if (
                    (placeholder_repair or authoritative_refresh)
                    and (title_changed or publish_date_changed or source_url_changed)
                ):
                    if title_changed:
                        existing.title = item.title
                    if publish_date_changed:
                        existing.publish_date = item.publish_date
                    if source_url_changed:
                        existing.source_url = item.source_url
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
                archive_updated_at=item.fetched_date,
                fetch_run_id=getattr(item, "fetch_run_id", None),
                job_id=getattr(item, "job_id", None),
                job_run_id=getattr(item, "job_run_id", None),
                source_group_id=getattr(item, "source_group_id", None),
                run_scope=getattr(item, "run_scope", "ad_hoc"),
                has_content=item.has_content,
                content=actual_content,
                extensions_json=json.dumps(extensions, ensure_ascii=False),
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
