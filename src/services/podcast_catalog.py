"""Curated podcast source catalog and safe SourceConfig bootstrap/importer.

The catalog is deliberately separate from the fetcher registry: every show uses the
same ``generic_podcast_rss`` execution path, while its stable identity and curation
metadata live here. Ready catalog entries are installed inactive at application
bootstrap so a fresh deployment has podcast nodes without unexpectedly starting
network collection; the import API remains available for selective operator actions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any, Iterable, Sequence

from sqlmodel import Session

from models.db import SourceConfigRecord


CATALOG_VERIFIED_AT = "2026-09-03"
DEFAULT_FETCH_LIMIT = 20
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class PodcastCatalogSource:
    source_id: str
    name: str
    feed_url: str
    publisher: str
    language: str
    topics: tuple[str, ...]
    source_scope: str
    provenance_tier: str
    launch_tier: str
    latest_episode_date: str
    description: str
    ingest_status: str = "ready"
    status_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        item = asdict(self)
        item["topics"] = list(self.topics)
        item["verified_at"] = CATALOG_VERIFIED_AT
        return item


def _source(
    slug: str,
    name: str,
    feed_url: str,
    publisher: str,
    language: str,
    topics: Sequence[str],
    source_scope: str,
    launch_tier: str,
    latest_episode_date: str,
    description: str,
    *,
    ingest_status: str = "ready",
    status_note: str = "",
) -> PodcastCatalogSource:
    if source_scope in {"ai_media", "tech_media"}:
        provenance_tier = "tier1_curated"
    elif source_scope in {
        "personal_commentary", "expert_commentary", "executive_commentary", "expert_newsletter",
    }:
        provenance_tier = "tier2_commentary"
    else:
        provenance_tier = "tier0_primary"
    return PodcastCatalogSource(
        source_id=f"podcast_{slug}",
        name=name,
        feed_url=feed_url,
        publisher=publisher,
        language=language,
        topics=tuple(topics),
        source_scope=source_scope,
        provenance_tier=provenance_tier,
        launch_tier=launch_tier,
        latest_episode_date=latest_episode_date,
        description=description,
        ingest_status=ingest_status,
        status_note=status_note,
    )


# 目录来自内部「欧研观澜」分析；feed URL 通过 Apple Podcasts/iTunes Search
# 返回的发布者分发地址反查，并在 CATALOG_VERIFIED_AT 做了 HTTP + XML + enclosure
# 实测。这里只保存抓取所需事实，不复制第三方平台的摘要或逐字稿。
PODCAST_CATALOG: tuple[PodcastCatalogSource, ...] = (
    _source("ai_daily_brief", "The AI Daily Brief", "https://anchor.fm/s/f7cac464/podcast/rss", "The AI Daily Brief", "en", ("AI news", "industry"), "ai_media", "core", "2026-09-02", "每日 AI 新闻与产业变化解读。"),
    _source("a16z_show", "The a16z Show", "https://feeds.simplecast.com/JGE3yC0V", "Andreessen Horowitz", "en", ("technology", "venture capital"), "company", "core", "2026-09-02", "a16z 关于技术、产业与创业的访谈。"),
    _source("latent_space", "Latent Space", "https://api.substack.com/feed/podcast/1084089.rss", "Latent Space", "en", ("AI engineering", "agents"), "ai_media", "core", "2026-08-26", "面向 AI 工程师的模型、Agent 与基础设施访谈。", status_note="feed 较大，导入参数使用 20 MiB 响应上限。"),
    _source("20vc", "20VC", "https://rss.libsyn.com/shows/61840/destinations/240976.xml", "20VC", "en", ("venture capital", "startups"), "tech_media", "extended", "2026-08-31", "风险投资、创业公司与科技商业访谈。", status_note="历史单集超过 1500，首轮抓取应保持 limit=20。"),
    _source("eye_on_ai", "Eye On A.I.", "https://rss.libsyn.com/shows/123267/destinations/727317.xml", "Eye On A.I.", "en", ("AI research", "industry"), "ai_media", "core", "2026-08-31", "AI 研究、产业与治理访谈。"),
    _source("dwarkesh", "Dwarkesh Podcast", "https://apple.dwarkesh-podcast.workers.dev/feed.rss", "Dwarkesh Patel", "en", ("AI research", "long-form interviews"), "expert_commentary", "core", "2026-09-01", "AI、科学与技术领域的深度长访谈。"),
    _source("all_in", "All-In", "https://rss.libsyn.com/shows/254861/destinations/1928300.xml", "All-In Podcast", "en", ("technology", "markets"), "tech_media", "extended", "2026-08-29", "科技、市场、政策与创业圆桌。"),
    _source("interconnects", "Interconnects", "https://api.substack.com/feed/podcast/48206.rss", "Nathan Lambert", "en", ("open models", "AI research"), "expert_newsletter", "core", "2026-08-12", "开放模型、强化学习与 AI 产业分析。"),
    _source("cognitive_revolution", "The Cognitive Revolution", "https://feeds.megaphone.fm/RINTP3108857801", "Turpentine", "en", ("AI research", "applications"), "ai_media", "core", "2026-09-01", "AI 技术进展及其现实应用访谈。"),
    _source("no_priors", "No Priors", "https://feeds.megaphone.fm/nopriors", "Conviction", "en", ("AI", "startups"), "company", "core", "2026-08-27", "AI 技术、公司与创业者访谈。"),
    _source("late_talk", "晚点聊 LateTalk", "https://feeds.fireside.fm/latetalk/rss", "晚点 LatePost", "zh", ("technology", "business"), "tech_media", "core", "2026-09-02", "科技商业与产业趋势中文播客。"),
    _source("ai_a16z", "AI + a16z", "https://feeds.simplecast.com/Hb_IuXOo", "Andreessen Horowitz", "en", ("AI", "enterprise"), "company", "core", "2026-08-07", "a16z 聚焦 AI 技术与商业落地的节目。"),
    _source("whats_next", "What's Next｜科技早知道", "https://feeds.fireside.fm/guiguzaozhidao/rss", "声动活泼", "zh", ("technology news", "industry"), "tech_media", "core", "2026-09-02", "全球科技趋势与产业新闻中文解读。"),
    _source("mlst", "Machine Learning Street Talk", "https://anchor.fm/s/1e4a0eac/podcast/rss", "Machine Learning Street Talk", "en", ("machine learning", "research"), "ai_media", "core", "2026-09-02", "机器学习研究者与技术思想访谈。"),
    _source("practical_ai", "Practical AI", "https://feeds.transistor.fm/practical-ai-machine-learning-data-science-llm", "Changelog Media", "en", ("AI engineering", "machine learning"), "tech_media", "core", "2026-08-28", "机器学习与 AI 工程实践。"),
    _source("talk_python", "Talk Python To Me", "https://talkpython.fm/episodes/rss", "Talk Python", "en", ("Python", "software engineering"), "tech_media", "extended", "2026-08-26", "Python 生态、数据与软件工程访谈。"),
    _source("twiml", "The TWIML AI Podcast", "https://feeds.megaphone.fm/MLN2155636147", "TWIML", "en", ("machine learning", "AI research"), "ai_media", "core", "2026-09-01", "机器学习研究与产业实践访谈。"),
    _source("silicon_valley_101", "硅谷101", "https://feeds.fireside.fm/sv101/rss", "硅谷101", "zh", ("technology", "business"), "tech_media", "core", "2026-08-27", "硅谷科技、商业与创新趋势中文访谈。"),
    _source("nvidia_ai", "NVIDIA AI Podcast", "https://feeds.megaphone.fm/nvidiaaipodcast", "NVIDIA", "en", ("AI", "accelerated computing"), "company", "core", "2026-06-24", "NVIDIA 官方 AI 技术与应用访谈。"),
    _source("hardcore_hackers", "硬地骇客", "https://feed.xyzfm.space/byhkljlbep9j", "硬地骇客", "zh", ("indie hacking", "software products"), "tech_media", "extended", "2026-08-02", "独立开发、软件产品与增长实践中文播客。"),
    _source("brain_wave", "脑放电波", "https://feed.xyzfm.space/wupdmt9er7nb", "脑放电波", "zh", ("AI", "technology"), "ai_media", "core", "2026-08-25", "AI 与前沿科技趋势中文圆桌。"),
    _source("ai_engineering", "AI Engineering Podcast", "https://serve.podhome.fm/rss/c9abdd38-a5dc-5eb2-96fd-f833f93208a7", "AI Engineering Podcast", "en", ("AI engineering", "data infrastructure"), "ai_media", "core", "2026-02-25", "AI 系统、数据与工程基础设施访谈。"),
    _source("42chapter", "42章经", "https://feed.xyzfm.space/evgg6xle9rdc", "42章经", "zh", ("startups", "technology"), "tech_media", "core", "2026-08-29", "创业、投资与科技趋势中文访谈。"),
    _source("lex_fridman", "Lex Fridman Podcast", "https://lexfridman.com/feed/podcast/", "Lex Fridman", "en", ("science", "technology"), "expert_commentary", "extended", "2026-08-26", "科学、技术与社会议题的长篇访谈。"),
    _source("techinsights_chip_observer", "TechInsights: The Chip-Observer", "https://anchor.fm/s/fee925b0/podcast/rss", "TechInsights", "en", ("semiconductors", "hardware"), "company", "core", "2026-06-22", "半导体、芯片与硬件产业观察。"),
    _source("moores_lobby", "Moore's Lobby", "https://rss.libsyn.com/shows/263636/destinations/2003405.xml", "All About Circuits", "en", ("semiconductors", "electronics"), "tech_media", "extended", "2026-08-18", "芯片、电路与电子工程师访谈。"),
    _source("voices_from_darpa", "Voices from DARPA", "https://feeds.blubrry.com/feeds/voices_from_darpa.xml", "DARPA", "en", ("research", "defense technology"), "research_lab", "extended", "2026-08-27", "DARPA 官方研究项目与项目经理访谈。", ingest_status="blocked", status_note="Apple 目录仍指向该 feed，但 2026-09-03 从当前网络访问发生 TLS EOF；默认不导入，待 CDN 恢复后复验。"),
    _source("asi_pill", "ASI pill", "https://feeds.redcircle.com/d86c0d6d-f462-42d0-bdc3-ee34f7812e41", "ASI pill", "en", ("AI news", "AI commentary"), "personal_commentary", "extended", "2026-09-02", "AI 新闻与观点型节目。"),
    _source("acquired", "Acquired", "https://feeds.transistor.fm/acquired", "Acquired", "en", ("technology companies", "business history"), "tech_media", "extended", "2026-08-09", "科技公司与商业史深度节目。"),
    _source("semianalysis_weekly", "SemiAnalysis Weekly", "https://anchor.fm/s/10fbee758/podcast/rss", "SemiAnalysis", "en", ("semiconductors", "AI infrastructure"), "ai_media", "core", "2026-09-02", "半导体与 AI 基础设施分析。"),
    _source("chain_of_thought", "Chain of Thought", "https://feeds.transistor.fm/chain-of-thought", "Chain of Thought", "en", ("AI agents", "AI infrastructure"), "ai_media", "core", "2026-09-02", "AI Agent、基础设施与工程访谈。"),
    _source("startup_project", "Startup Project", "https://anchor.fm/s/4126a980/podcast/rss", "Startup Project", "en", ("startups", "technology"), "expert_commentary", "extended", "2026-08-27", "科技创业者与公司构建访谈。"),
    _source("ted_ai_show", "The TED AI Show", "https://feeds.acast.com/public/shows/6758564a102e6d4448d19589", "TED", "en", ("AI", "society"), "ai_media", "core", "2026-07-10", "TED 关于 AI 技术及社会影响的节目。"),
    _source("gradient_dissent", "Gradient Dissent", "https://feeds.captivate.fm/gradient-dissent/", "Weights & Biases", "en", ("machine learning", "MLOps"), "company", "core", "2026-08-18", "机器学习研究、工程与 MLOps 访谈。"),
    _source("inside_ai", "Inside AI", "https://feeds.async.com/5ed1350c-adaf-4a49-8cf8-3c221320cd8a.rss", "Inside AI", "en", ("AI", "technology"), "ai_media", "extended", "2025-01-13", "AI 公司与技术人物访谈。", status_note="feed 可用但自 2025-01 起未见更新，建议低优先级观察。"),
    _source("telco_in_20", "Telco in 20", "https://feeds.simplecast.com/MAc9YZnG", "The Mobile Network", "en", ("telecom", "networking"), "tech_media", "extended", "2026-09-01", "电信、网络与通信产业访谈。"),
)


def catalog_by_id() -> dict[str, PodcastCatalogSource]:
    return {item.source_id: item for item in PODCAST_CATALOG}


def list_podcast_catalog(session: Session | None = None) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    installed_count = 0
    active_count = 0
    for source in PODCAST_CATALOG:
        row = session.get(SourceConfigRecord, source.source_id) if session is not None else None
        installed = row is not None
        active = bool(row and row.is_active)
        installed_count += int(installed)
        active_count += int(active)
        items.append({**source.to_dict(), "installed": installed, "active": active})
    return {
        "verified_at": CATALOG_VERIFIED_AT,
        "total": len(items),
        "ready": sum(item["ingest_status"] == "ready" for item in items),
        "blocked": sum(item["ingest_status"] != "ready" for item in items),
        "installed": installed_count,
        "active": active_count,
        "items": items,
    }


def _record_params(source: PodcastCatalogSource) -> dict[str, Any]:
    return {
        "catalog": "ouyan-guanlan-2026-09",
        "catalog_verified_at": CATALOG_VERIFIED_AT,
        "language": source.language,
        "launch_tier": source.launch_tier,
        "limit": DEFAULT_FETCH_LIMIT,
        "max_response_bytes": DEFAULT_MAX_RESPONSE_BYTES,
    }


def _apply_catalog_fields(record: SourceConfigRecord, source: PodcastCatalogSource, now: str) -> None:
    record.name = source.name
    record.source_type = "podcast"
    record.url = source.feed_url
    # SourceConfig category controls collection operations, so every newly curated
    # source follows the repository's mandatory observation period.
    record.category = "incubating"
    record.fetcher_id = "generic_podcast_rss"
    record.description = source.description
    record.source_owner = source.publisher
    record.source_brand = source.name
    record.source_scope = source.source_scope
    record.source_channel = "podcast_rss"
    record.base_url = source.feed_url
    record.provenance_tier = source.provenance_tier
    record.content_tags_json = json.dumps(list(source.topics), ensure_ascii=False)
    record.signal_strength = "high" if source.launch_tier == "core" else "medium"
    record.noise_risk = "low" if source.launch_tier == "core" else "medium"
    record.fetch_reliability = "high" if source.ingest_status == "ready" else "blocked"
    record.fetch_interval_minutes = 360
    record.cron_expr = ""
    record.params_json = json.dumps(_record_params(source), ensure_ascii=False, sort_keys=True)
    record.updated_at = now


def import_podcast_catalog(
    session: Session,
    *,
    source_ids: Iterable[str] | None = None,
    activate: bool = False,
    update_existing: bool = False,
    include_blocked: bool = False,
) -> dict[str, Any]:
    """Idempotently import selected catalog rows into ``source_configs``.

    With no explicit IDs, all currently ready sources are selected. Existing rows
    are never changed unless ``update_existing`` is true. Blocked sources require an
    explicit ``include_blocked`` opt-in even if their IDs were supplied.
    """
    by_id = catalog_by_id()
    requested = list(dict.fromkeys(str(item).strip() for item in (source_ids or []) if str(item).strip()))
    unknown = sorted(set(requested) - set(by_id))
    if unknown:
        raise ValueError(f"未知播客目录 source_id: {', '.join(unknown)}")
    selected = [by_id[source_id] for source_id in requested] if requested else list(PODCAST_CATALOG)

    created: list[str] = []
    updated: list[str] = []
    skipped_existing: list[str] = []
    skipped_blocked: list[dict[str, str]] = []
    now = datetime.now().isoformat()

    for source in selected:
        if source.ingest_status != "ready" and not include_blocked:
            skipped_blocked.append({"source_id": source.source_id, "reason": source.status_note})
            continue
        record = session.get(SourceConfigRecord, source.source_id)
        if record is not None and not update_existing:
            skipped_existing.append(source.source_id)
            continue
        if record is None:
            record = SourceConfigRecord(
                source_id=source.source_id,
                name=source.name,
                created_at=now,
                updated_at=now,
            )
            created.append(source.source_id)
        else:
            updated.append(source.source_id)
        previous_active = bool(record.is_active)
        _apply_catalog_fields(record, source, now)
        # Updating metadata must not silently disable an already active source.
        record.is_active = bool(activate or previous_active) if source.source_id in updated else activate
        session.add(record)

    session.commit()
    return {
        "selected": len(selected),
        "created": created,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_blocked": skipped_blocked,
        "activate": activate,
        "update_existing": update_existing,
    }


def ensure_default_podcast_sources(engine: Any) -> dict[str, Any]:
    """Install all ready catalog entries once, inactive and without overwrites.

    This intentionally delegates to the same idempotent importer exposed to admins:
    new catalog additions appear after a later restart, while local edits, activation
    choices, and the explicitly blocked catalog entry remain untouched.
    """
    with Session(engine) as session:
        return import_podcast_catalog(
            session,
            activate=False,
            update_existing=False,
            include_blocked=False,
        )
