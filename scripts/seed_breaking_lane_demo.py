"""Seed a **sandbox** database with the breaking-lane acceptance scenario (issue #33 §2, v3.50).

用法(只对沙箱库,拒绝对 cms_data.db 直接下手):

    cp data/cms_data.db /tmp/dorami-sandbox.db
    .venv/bin/python scripts/seed_breaking_lane_demo.py --db /tmp/dorami-sandbox.db
    .venv/bin/python scripts/seed_breaking_lane_demo.py --db /tmp/dorami-sandbox.db --yesterday-headline

脚本会:先 ensure_migrated 把沙箱升到 head;建读者 `breaking_demo`(默认密码 demo1234,跳过兴趣引导、
不播种默认订阅);只订阅一个演示源 `demo_own_blog`;再灌入一组「近几小时」发布、分析已完成的演示文章:

    demo_own_blog          8.2 / 7.6   → 读者自己的精选(quality 通道)
    rss_openai_news        9.6  entity.openai   官方文章 ┐ 同一事件(共享 entity.openai)
    x_openai               9.8  entity.openai   官方推文 ┘ 代表应取官方文章而非推文
    rss_testingcatalog     9.2  entity.anthropic 媒体   ┐ 多源印证:两家 ≥ T−0.5 且一条 ≥ T
    web_aiera              8.7  entity.anthropic 媒体   ┘
    rss_deepmind_blog      9.1  entity.google-deepmind 官方(第三个事件,默认 2 条上限时被裁掉)
    web_qbitai             9.3  无实体           单一媒体高分 → 不进(无官方、无印证)
    user_rss_demo_private  9.9                   私有自定源 → 不进

预期(默认旋钮 T=9.0 / N=2):早报前两条为「重大事件」= OpenAI 官方文章(official)+ TestingCatalog(corroborated,
2 家),随后是 demo_own_blog 的两篇精选;`--yesterday-headline` 再跑一次则 OpenAI 事件被前一日头条抑制,
头条变为 TestingCatalog + DeepMind。默认把沙箱的 article_analysis_enabled 置 false,免得 worker 去重评演示文章。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlalchemy import create_engine, delete  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from llm.article_analysis_prompt import (  # noqa: E402
    ARTICLE_ANALYSIS_PROMPT_VERSION,
    ARTICLE_ANALYSIS_SCORING_VERSION,
)
from models.db import (  # noqa: E402
    AppSettingRecord,
    ArticleAnalysisRecord,
    ArticleRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    PersonalDigestEditionRecord,
    PersonalDigestItemRecord,
    ReaderSubscriptionRecord,
    UserRecord,
)
from services import accounts as accounts_service  # noqa: E402
from services.article_analysis import compute_content_hash  # noqa: E402
from storage.migrations import ensure_migrated  # noqa: E402

SHANGHAI = ZoneInfo("Asia/Shanghai")
PREFIX = "demo-breaking-"
OWN_SOURCE = "demo_own_blog"

# (suffix, source_id, content_type, score, title, entity codes, genre)
ARTICLES = [
    ("own-1", OWN_SOURCE, "rss_article", 8.2, "演示博客:Agent 评测方法论的三个坑", (), "opinion"),
    ("own-2", OWN_SOURCE, "rss_article", 7.6, "演示博客:本周读到的五篇论文", (), "aggregation"),
    ("openai-blog", "rss_openai_news", "rss_article", 9.6, "GPT-6 Astra: A new generation of intelligence", ("entity.openai",), "model_release"),
    ("openai-x", "x_openai", "social_post", 9.8, "This is GPT-6 Astra.", ("entity.openai",), "model_release"),
    ("fable-tc", "rss_testingcatalog", "rss_article", 9.2, "Anthropic launches Claude Fable 5.1 and Mythos 5.1", ("entity.anthropic",), "model_release"),
    ("fable-aiera", "web_aiera", "web_article", 8.7, "刚刚，神话级 Fable 5.1 来了！", ("entity.anthropic",), "model_release"),
    ("deepmind", "rss_deepmind_blog", "rss_article", 9.1, "Introducing WeatherNext 3", ("entity.google-deepmind",), "model_release"),
    ("lone-media", "web_qbitai", "web_article", 9.3, "李飞飞发布：全球首个多模态世界模型", (), "industry_news"),
    ("private", "user_rss_demo_private", "rss_article", 9.9, "私有源里的大新闻(不应出现)", (), "industry_news"),
]
ENTITY_NAMES = {
    "entity.openai": "OpenAI",
    "entity.anthropic": "Anthropic",
    "entity.google-deepmind": "Google DeepMind",
}


def _now_iso() -> str:
    return dt.datetime.now(SHANGHAI).isoformat()


def _ensure_tag(session: Session, code: str, now: str) -> CmsTagRecord:
    tag = session.exec(select(CmsTagRecord).where(CmsTagRecord.code == code)).first()
    if tag is None:
        tag = CmsTagRecord(
            code=code, kind="entity", name_zh=ENTITY_NAMES.get(code, code), name_en=ENTITY_NAMES.get(code, code),
            normalized_name=code, status="active", user_selectable=True, entity_type="organization",
            created_at=now, updated_at=now,
        )
        session.add(tag)
        session.flush()
    return tag


def _wipe(session: Session, reader: str) -> None:
    demo_ids = [row for row in session.exec(select(ArticleRecord.id).where(ArticleRecord.id.like(f"{PREFIX}%"))).all()]
    if demo_ids:
        session.exec(delete(ArticleTagAssignmentRecord).where(ArticleTagAssignmentRecord.article_id.in_(demo_ids)))
        session.exec(delete(ArticleAnalysisRecord).where(ArticleAnalysisRecord.article_id.in_(demo_ids)))
        session.exec(delete(PersonalDigestItemRecord).where(PersonalDigestItemRecord.article_id.in_(demo_ids)))
        session.exec(delete(ArticleRecord).where(ArticleRecord.id.in_(demo_ids)))
    edition_ids = [row for row in session.exec(
        select(PersonalDigestEditionRecord.id).where(PersonalDigestEditionRecord.owner_username == reader)
    ).all()]
    if edition_ids:
        session.exec(delete(PersonalDigestItemRecord).where(PersonalDigestItemRecord.edition_id.in_(edition_ids)))
        session.exec(delete(PersonalDigestEditionRecord).where(PersonalDigestEditionRecord.id.in_(edition_ids)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", required=True, help="沙箱 SQLite 文件路径(拒绝 cms_data.db)")
    parser.add_argument("--reader", default="breaking_demo")
    parser.add_argument("--password", default="demo1234")
    parser.add_argument("--hours-ago", type=float, default=3.0, help="演示文章的发布时间 = 现在 − N 小时(须 < 24)")
    parser.add_argument("--yesterday-headline", action="store_true", help="给读者预置一期昨日早报,头条实体 entity.openai,演示跨天抑制")
    parser.add_argument("--keep-worker", action="store_true", help="不关闭沙箱的 article_analysis_enabled")
    args = parser.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    if db_path.name == "cms_data.db":
        print("拒绝:请复制一份沙箱库再运行(cp data/cms_data.db /tmp/dorami-sandbox.db)", file=sys.stderr)
        return 2
    if not db_path.exists():
        print(f"沙箱库不存在:{db_path}", file=sys.stderr)
        return 2
    db_url = f"sqlite:///{db_path}"
    ensure_migrated(db_url)
    engine = create_engine(db_url)
    now = _now_iso()
    published = (dt.datetime.now(SHANGHAI) - dt.timedelta(hours=args.hours_ago)).isoformat()

    with Session(engine) as session:
        _wipe(session, args.reader)
        if session.get(UserRecord, args.reader) is None:
            accounts_service.create_user(session, args.reader, args.password, "user")
        user = session.get(UserRecord, args.reader)
        user.interest_onboarding_completed_at = user.interest_onboarding_completed_at or now
        session.add(user)
        for key, value in (
            (f"reader_defaults_seeded:{args.reader}", now),
            ("personal_digest_enabled", "true"),
        ):
            row = session.get(AppSettingRecord, key) or AppSettingRecord(key=key)
            row.value = value
            session.add(row)
        if not args.keep_worker:
            row = session.get(AppSettingRecord, "article_analysis_enabled") or AppSettingRecord(key="article_analysis_enabled")
            row.value = "false"
            session.add(row)
        existing_sub = session.exec(select(ReaderSubscriptionRecord).where(
            ReaderSubscriptionRecord.owner_username == args.reader,
            ReaderSubscriptionRecord.name == "breaking-demo-own",
        )).first()
        if existing_sub is None:
            session.add(ReaderSubscriptionRecord(
                owner_username=args.reader, name="breaking-demo-own",
                filters_json=json.dumps({"source_ids": OWN_SOURCE}),
                token_hash=f"breaking-demo-{args.reader}", is_active=True,
                created_at=now, updated_at=now,
            ))
        tags = {code: _ensure_tag(session, code, now) for code in ENTITY_NAMES}
        for suffix, source_id, content_type, score, title, codes, genre in ARTICLES:
            article = ArticleRecord(
                id=f"{PREFIX}{suffix}", title=title, content_type=content_type, source_id=source_id,
                source_url=f"https://example.invalid/{suffix}", publish_date=published, fetched_date=published,
                content=f"演示正文({suffix}):{title}。",
            )
            session.add(article)
            session.flush()
            session.add(ArticleAnalysisRecord(
                article_id=article.id, status="succeeded", tagging_status="succeeded",
                quality_score=score, score_reason="演示数据:按剧本给定的新闻价值分。",
                summary=f"演示摘要:{title}", content_genre=genre,
                content_hash=compute_content_hash(article), model_name="demo",
                prompt_version=ARTICLE_ANALYSIS_PROMPT_VERSION, scoring_version=ARTICLE_ANALYSIS_SCORING_VERSION,
                analyzed_at=now, created_at=now, updated_at=now,
            ))
            for index, code in enumerate(codes):
                session.add(ArticleTagAssignmentRecord(
                    article_id=article.id, tag_id=tags[code].id, tag_kind="entity", relevance=0.95,
                    is_primary=index == 0, created_at=now, updated_at=now,
                ))
        if args.yesterday_headline:
            yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
            stamp = f"{yesterday}T08:30:00+08:00"
            edition = PersonalDigestEditionRecord(
                owner_username=args.reader, report_date=yesterday, revision=1, status="ready",
                check_after=stamp, cutoff_at=stamp, generated_at=stamp, created_at=stamp, updated_at=stamp,
                generation_reason="scheduled", policy_version="personal-digest-v1",
            )
            session.add(edition)
            session.flush()
            session.add(PersonalDigestItemRecord(
                edition_id=edition.id, article_id=None, position=0, section="重大事件", selection_lane="breaking",
                quality_score_snapshot=9.8, ranking_features_json=json.dumps({"event_entity_codes": ["entity.openai"]}),
                snapshot_json=json.dumps({"title": "(昨日头条演示)GPT-6 Astra", "source_name": "OpenAI 新闻"}),
                created_at=stamp,
            ))
        session.commit()

    print(f"沙箱已就绪:{db_path}")
    print(f"读者:{args.reader} / {args.password}(只订阅 {OWN_SOURCE})")
    print("预期头条:", "TestingCatalog(印证) + DeepMind(官方,OpenAI 被昨日头条抑制)" if args.yesterday_headline
          else "OpenAI 官方文章(official,代表非推文) + TestingCatalog(corroborated,2 家)")
    print("不应出现:web_qbitai 单源 9.3、user_rss_demo_private 9.9、x_openai 推文(被官博代表)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
