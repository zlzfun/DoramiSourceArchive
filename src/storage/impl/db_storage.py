import json
import os
from typing import Optional, Dict, Any, Iterable
from sqlalchemy import inspect, text, event
from sqlalchemy.pool import NullPool
from sqlalchemy.exc import InvalidRequestError
from sqlmodel import Session, create_engine, select
from storage.base import BaseStorage
from models.content import BaseContent, serialize_to_metadata
from models.db import (
    ArticleAnalysisRecord,
    ArticleRecord,
    SQLModel,
    SourceConfigRecord,
    next_article_archive_revision,
)
from services.podcast_metadata import merge_podcast_publisher_metadata
from services import item_url_identity, social_merge, source_catalog, source_rows, sync_consumer_policy
from services.title_cache import extensions as title_extensions, preserve_title_receipt
from services.collection_nodes import PODCAST_SOURCE_TYPES, is_public_podcast_source
from services.podcast_stage_policy import PodcastStageDenied, require_stage as require_podcast_stage


_PLACEHOLDER_ARTICLE_TITLES = {
    "",
    "未命名网页条目",
    "read more",
    "learn more",
    "más información",
}

def _is_placeholder_article_title(value: str) -> bool:
    return (value or "").strip().casefold() in _PLACEHOLDER_ARTICLE_TITLES


# ``save_with_outcome`` results.  Only ``SAVE_STORED_OUTCOMES`` count as "stored" for the
# fetch pipeline's saved_count / downstream queues (historic ``save() -> True``).
SAVE_CREATED = "created"
SAVE_CONTENT_FILLED = "content_filled"
SAVE_METADATA_REPAIRED = "metadata_repaired"
SAVE_STRUCTURE_FILLED = "structure_filled"
SAVE_DUPLICATE = "duplicate"
SAVE_REFRESHED = "refreshed"
SAVE_ID_CONFLICT = "id_conflict"
SAVE_REJECTED_AUTHORITY = "rejected_authority"
SAVE_REJECTED_PODCAST_SOURCE = "rejected_podcast_source"
SAVE_REJECTED_PODCAST_STAGE = "rejected_podcast_stage"
SAVE_REJECTED_RETIRED = "rejected_retired"
SAVE_STORED_OUTCOMES = frozenset({
    SAVE_CREATED, SAVE_CONTENT_FILLED, SAVE_METADATA_REPAIRED, SAVE_STRUCTURE_FILLED,
})
SAVE_OUTCOMES = SAVE_STORED_OUTCOMES | {
    SAVE_DUPLICATE,
    SAVE_REFRESHED,
    SAVE_ID_CONFLICT,
    SAVE_REJECTED_AUTHORITY,
    SAVE_REJECTED_PODCAST_SOURCE,
    SAVE_REJECTED_PODCAST_STAGE,
    SAVE_REJECTED_RETIRED,
}


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
        engine_options = {}
        if is_sqlite and db_url != "sqlite:///:memory:":
            # File-backed SQLite connections are cheap and the dashboard opens many
            # read endpoints at once.  Keeping the default 5+10 QueuePool creates an
            # artificial ceiling where request-scoped sessions can all wait for a
            # pooled connection.  NullPool still closes each connection with its
            # Session, while allowing the web/thread concurrency to be the limit.
            engine_options["poolclass"] = NullPool
        self.engine = create_engine(
            db_url,
            echo=False,
            connect_args=connect_args,
            **engine_options,
        )
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
            from storage.podcast_qa_fts import ensure_podcast_qa_fts
            ensure_podcast_qa_fts(self.engine)
        from services import title_translation
        title_translation.initialize(self.engine)
        self.logger.info(f"🗄️ 关系型数据库已连接: {db_url}")

    def _queue_refreshed_podcast_analysis(self, article_id: str) -> None:
        """Queue a metadata-only Podcast refresh without reporting a new save."""

        try:
            from services import article_analysis

            with Session(self.engine) as session:
                dirty_changed = article_analysis.mark_podcast_people_dirty(
                    session, article_id
                )
                enabled = article_analysis.read_feature_flag(
                    session,
                    article_analysis.ARTICLE_ANALYSIS_ENABLED_KEY,
                    default=False,
                )
                if not enabled:
                    if dirty_changed:
                        session.commit()
                    return
                article_analysis.queue_article_analysis(session, article_id)
                session.commit()
        except Exception as exc:  # noqa: BLE001 - metadata is already durable
            self.logger.warning(
                "Podcast metadata refresh analysis queue failed (article_id=%s): %s",
                article_id,
                type(exc).__name__,
            )

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

    @staticmethod
    def _claim_local_version(session: Session, article: ArticleRecord) -> None:
        """本机抓取 / 收录改动了一篇同步写入的文章:它从此是本机版本(issue #263)。

        清掉文章与其分析行上的同步印记——印记只表示「当前版本是同步写进来的」。之后本机按自己的
        开关处理它;上游若出现修订号更新的版本,同步时再接管回去。
        """

        if not (article.analysis_authority_id or "").strip():
            return
        article.analysis_authority_id = ""
        analysis = session.get(ArticleAnalysisRecord, article.id)
        if analysis is not None and (analysis.authority_id or "").strip():
            analysis.authority_id = ""
            analysis.authority_revision = ""
            session.add(analysis)

    @staticmethod
    def _lock_existing_for_rewrite(session: Session, existing: ArticleRecord) -> bool:
        """改写一篇已有文章之前:先取写锁,再把这一行重读成最新已提交的样子(issue #263)。

        ``existing`` 是在事务外读到的(pysqlite 到第一条写语句才开事务)。节点既抓取又同步之后,
        「读到本机版本 → 决定补正文 / 修元数据 → 写回」之间,同步可能已经把这篇换成了上游的版本;
        拿旧对象写回会得到「上游的印记 + 本机的正文」——上游的分析随后因哈希不符整页失败。
        取锁后重读,调用方按最新的行重新判定。内存里尚未落库的改动一并丢弃。返回 False =
        这一行在等锁期间已经不在了(被删除),调用方按「本轮不处理」收工,下一轮按新条目入库。
        """

        session.expire(existing)
        session.connection().exec_driver_sql(
            "UPDATE archive_sync_clock SET revision = revision WHERE id = 1"
        )
        try:
            session.refresh(existing)
        except InvalidRequestError:
            return False
        return True

    def _complete_social_post(
        self, session: Session, existing: ArticleRecord, item: BaseContent
    ) -> Optional[str]:
        """同一条社交帖再次到达:把收录时不完整的条目补全(issue #341)。

        推送的推文常缺引用帖、原帖或配图,而「同一条已有正文」一律按重复处理——重推补不上,抓取器随后
        抓到的完整版也进不来,库里一直是不完整的那条。两条规则(``services/social_merge``):

        - 收录条目再次被收录:只补缺的结构,已有的值与正文不动;
        - 抓取器抓到同一条收录条目:整体换成抓取版(正文、标题、发布时间、链接与社交键)。

        抓取入库的条目、已换成抓取版的条目不在此列。到达时间 ``fetched_date`` 不动——补全不该让
        条目重新冒成新到的。返回 None = 没有可补的,调用方按原有路径继续。
        """

        def plan() -> Optional[tuple[Dict[str, Any], bool]]:
            if (existing.ingest_channel or "") not in item_url_identity.INGEST_CHANNELS:
                return None
            try:
                current = json.loads(existing.extensions_json or "{}")
            except (TypeError, ValueError):
                return None
            if not isinstance(current, dict) or social_merge.has_platform_raw(current):
                return None
            incoming = serialize_to_metadata(item).get("extensions", {})
            if getattr(item, "_ingest_channel", ""):
                merged = social_merge.fill_missing(current, incoming)
                return None if merged is None else (merged, False)
            merged = preserve_title_receipt(
                existing, social_merge.adopt_fetched(current, incoming), title=item.title or existing.title,
            )
            changed = (
                merged != current
                or bool(item.title and item.title != existing.title)
                or bool(item.has_content and item.content and item.content != existing.content)
                or bool(item.publish_date and item.publish_date != existing.publish_date)
                or bool(item.source_url and item.source_url != existing.source_url)
            )
            return (merged, True) if changed else None

        if plan() is None:
            return None
        # 取锁重读后按最新的行重新判定一次(与补正文 / 修元数据同一做法)。
        if not self._lock_existing_for_rewrite(session, existing):
            return SAVE_DUPLICATE
        planned = plan()
        if planned is None:
            return SAVE_DUPLICATE
        merged, adopt = planned
        if adopt:
            if item.title:
                existing.title = item.title
            if item.has_content and item.content:
                existing.content = item.content
                existing.has_content = True
            if item.publish_date:
                existing.publish_date = item.publish_date
            if item.source_url:
                existing.source_url = item.source_url
        existing.extensions_json = json.dumps(merged, ensure_ascii=False)
        self._claim_local_version(session, existing)
        existing.archive_updated_at = next_article_archive_revision(existing.archive_updated_at)
        session.add(existing)
        session.commit()
        return SAVE_STRUCTURE_FILLED

    async def save(self, item: BaseContent) -> bool:
        """Pipeline insertion signal: true only when a row was created or filled."""
        return (await self.save_with_outcome(item)) in SAVE_STORED_OUTCOMES

    async def save_with_outcome(self, item: BaseContent) -> str:
        """Persist one item and report why it was (not) stored.

        Outcomes are listed in ``SAVE_OUTCOMES``; ``save()`` keeps the historic
        boolean contract for the fetch pipeline, while ingest endpoints map the
        string to a precise error instead of a blanket "ID already exists".
        """
        with Session(self.engine) as session:
            if not sync_consumer_policy.local_source_operation_allowed(
                session, item.source_id, operation="collection"
            ):
                # A fetch that started before an Archive Sync authority handoff
                # or v2 consumer activation must not commit afterwards.
                return SAVE_REJECTED_AUTHORITY
            source_row = session.get(SourceConfigRecord, item.source_id) if item.source_id else None
            if source_row is not None and source_row.retired_at:
                # 退役源停止新增(删除改退役后不再有物理删除挡住它),历史条目照旧可读。
                return SAVE_REJECTED_RETIRED
            if getattr(item, "content_type", "") == "podcast_episode":
                source = session.get(SourceConfigRecord, item.source_id)
                if (
                    source is None
                    or (source.source_type or "").strip().lower() not in PODCAST_SOURCE_TYPES
                    or (not source.is_active and not is_public_podcast_source(source))
                ):
                    # This is the last transactional fence before a fetched
                    # Podcast episode becomes durable.  False tells the pipeline
                    # that no item was committed and keeps it out of downstream
                    # analysis/media queues.
                    return SAVE_REJECTED_PODCAST_SOURCE
                try:
                    require_podcast_stage("fetch", boundary="commit")
                except PodcastStageDenied:
                    return SAVE_REJECTED_PODCAST_STAGE
            existing = session.get(ArticleRecord, item.id)
            if existing is None and item.source_id and item.source_url:
                # 先推后抓合流(issue #262):同一个源里已有同链接的收录条目(推送 / 手工 / 导入,
                # 条目 id 公式可能与抓取器不同)时按同一条处理——补正文 / 修元数据 / 幂等跳过与 id
                # 命中同一口径。改写 item.id 让管线把后续分析与媒体预取挂到已有行上。
                alias = item_url_identity.ingested_row_for(
                    session,
                    item.source_id,
                    item.source_url,
                    getattr(item, "content_type", "") or "",
                )
                if alias is not None:
                    item.id = alias.id
                    existing = alias
            if existing:
                if (existing.source_id or "") != (item.source_id or ""):
                    # Item ids are only unique per source by convention.  A row
                    # owned by another source is never rewritten in place (that
                    # would move a faithful archive entry under a foreign body).
                    return SAVE_ID_CONFLICT
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
                        return SAVE_DUPLICATE
                    # 有东西要并:取锁重读,在最新的行上再并一次(上面那次改的可能是过时的对象)。
                    if not self._lock_existing_for_rewrite(session, existing):
                        return SAVE_DUPLICATE
                    if not merge_podcast_publisher_metadata(
                        existing,
                        item,
                        serialize_to_metadata(item).get("extensions", {}),
                    ):
                        return SAVE_DUPLICATE
                    self._claim_local_version(session, existing)
                    existing.archive_updated_at = next_article_archive_revision(
                        existing.archive_updated_at
                    )
                    session.add(existing)
                    session.commit()
                    self._queue_refreshed_podcast_analysis(existing.id)
                    # ``save`` is the pipeline's insertion signal.  The refresh was
                    # persisted, but must not inflate saved_count/saved_content_ids.
                    return SAVE_REFRESHED
                if not existing.has_content and item.has_content and item.content:
                    if not self._lock_existing_for_rewrite(session, existing) or existing.has_content:
                        # 等锁期间正文已经被补上(同步或另一轮抓取):按重复处理。
                        return SAVE_DUPLICATE
                    raw_metadata = serialize_to_metadata(item)
                    existing.title = item.title
                    existing.source_url = item.source_url
                    existing.publish_date = item.publish_date
                    existing.fetched_date = item.fetched_date
                    self._claim_local_version(session, existing)
                    existing.archive_updated_at = next_article_archive_revision(
                        existing.archive_updated_at
                    )
                    existing.has_content = True
                    existing.content = item.content
                    existing.extensions_json = json.dumps(preserve_title_receipt(
                        existing, raw_metadata.get("extensions", {}), title=item.title,
                    ), ensure_ascii=False)
                    session.add(existing)
                    session.commit()
                    return SAVE_CONTENT_FILLED
                if (
                    existing.content_type == "social_post"
                    and getattr(item, "content_type", "") == "social_post"
                ):
                    completed = self._complete_social_post(session, existing, item)
                    if completed is not None:
                        return completed
                # 两条极窄的元数据自愈路径：① 旧标题明确是 CTA/占位文案；
                # ② 抓取器显式声明官方元数据权威。普通源默认仍幂等跳过，
                # 绝不因上游改标题而改写忠实归档；两路都不覆盖正文。
                def repair_plan() -> tuple[bool, bool, bool, bool]:
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
                    wanted = (placeholder_repair or authoritative_refresh) and (
                        title_changed or publish_date_changed or source_url_changed
                    )
                    return wanted, title_changed, publish_date_changed, source_url_changed

                wanted, title_changed, publish_date_changed, source_url_changed = repair_plan()
                if wanted:
                    # 取锁重读后按最新的行重新判定一次。
                    if not self._lock_existing_for_rewrite(session, existing):
                        return SAVE_DUPLICATE
                    wanted, title_changed, publish_date_changed, source_url_changed = repair_plan()
                if wanted:
                    if title_changed:
                        existing.title = item.title
                    if publish_date_changed:
                        existing.publish_date = item.publish_date
                    if source_url_changed:
                        existing.source_url = item.source_url
                    self._claim_local_version(session, existing)
                    existing.archive_updated_at = next_article_archive_revision(
                        existing.archive_updated_at
                    )
                    session.add(existing)
                    session.commit()
                    return SAVE_METADATA_REPAIRED
                return SAVE_DUPLICATE

            # 源表全集不变量(issue #262 R1c):新条目入库前在同一事务里确保源行存在。注入通道
            # 经 ``_source_hint`` 带出处描述(名称 / 头像 / 平台 / 来路);抓取路径不带,未登记的
            # id 按存量口径记 legacy(与今天「只在 articles 里出现的源」同样列出)。接收端不建。
            hint = dict(getattr(item, "_source_hint", None) or {})
            if not hint:
                hint = {
                    "origin": source_catalog.ORIGIN_LEGACY,
                    "admission_status": "admitted",
                }
            source_rows.ensure_source_row(
                session,
                item.source_id,
                content_type=getattr(item, "content_type", "") or "",
                **hint,
            )
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
                # 条目来路(issue #262 波 1):只由服务端注入通道挂在实例上,不进 extensions。
                ingest_channel=str(getattr(item, "_ingest_channel", "") or "")[:20],
                ingest_actor=str(getattr(item, "_ingest_actor", "") or "")[:80],
            )
            session.add(record)
            # Persist the optional paper task in the same transaction as the
            # abstract. HTTP/HTML work runs independently after ingestion.
            from services.paper_fulltext import enqueue as enqueue_paper_fulltext
            enqueue_paper_fulltext(session, record)
            session.commit()
            return SAVE_CREATED

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
            updates = dict(updates)
            if "extensions_json" in updates:
                incoming = title_extensions({"extensions_json": updates["extensions_json"]})
                updates["extensions_json"] = json.dumps(preserve_title_receipt(
                    record, incoming, title=updates.get("title", record.title),
                ), ensure_ascii=False)
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
