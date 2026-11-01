"""MCP get_user_context 工具(issue #175 v1):四段阅读侧上下文的实时拼装。

覆盖:每段内容、各段条目上限、整体 token 预算裁剪(低优先级段先丢)、空用户、
收藏/阅读时间窗、用户隔离、令牌门控(缺令牌 / 未接线 resolver 一律拒绝)。
"""
import datetime
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from sqlmodel import Session

from storage.impl.db_storage import DatabaseStorage
from models.db import (
    ArticleRecord,
    ArticleAnalysisRecord,
    ArticleTagAssignmentRecord,
    CmsTagRecord,
    ReaderFavoriteRecord,
    ReaderReadRecord,
    ReaderSubscriptionRecord,
    UserInterestTagRecord,
    UserRecord,
)

from mcp_server import _get_user_context_impl, build_mcp_app


DEFAULTS = {
    "interest_tag_limit": 20,
    "favorite_limit": 10,
    "favorite_days": 14,
    "source_limit": 8,
    "source_days": 14,
    "subscription_limit": 10,
    "max_tokens": 800,
}


def make_db_sink():
    return DatabaseStorage(db_url="sqlite:///:memory:")


def _ensure_user(db_sink, username):
    """user_interest_tags.owner_username 外键指向 users.username,先种账户行。"""
    from services import accounts as accounts_service
    now = _today_iso()
    with Session(db_sink.engine) as session:
        if session.get(UserRecord, username) is None:
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password(username),
                role="user",
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
            session.commit()


def _today_iso() -> str:
    return datetime.datetime.now().isoformat()


def _days_ago(days: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()


def seed_cms_tag(session, name_zh, *, status="active", code=None):
    tag = CmsTagRecord(
        code=code or name_zh,
        kind="topic",
        name_zh=name_zh,
        normalized_name=name_zh,
        status=status,
        created_at=_today_iso(),
        updated_at=_today_iso(),
    )
    session.add(tag)
    session.commit()
    session.refresh(tag)
    session.expunge(tag)
    return tag


def seed_article(db_sink, article_id, title, *, content="body text"):
    with Session(db_sink.engine) as session:
        session.add(ArticleRecord(
            id=article_id,
            title=title,
            source_id="src_x",
            content_type="article",
            source_url=f"http://example.com/{article_id}",
            publish_date=str(datetime.date.today()),
            fetched_date=str(datetime.date.today()),
            has_content=bool(content),
            content=content,
        ))
        session.commit()
    return article_id


def seed_interest(db_sink, username, tag):
    _ensure_user(db_sink, username)
    with Session(db_sink.engine) as session:
        session.add(UserInterestTagRecord(
            owner_username=username,
            tag_id=int(tag.id),
            created_at=_today_iso(),
            updated_at=_today_iso(),
        ))
        session.commit()


def seed_favorite(db_sink, username, article_id, *, days_ago=0):
    with Session(db_sink.engine) as session:
        session.add(ReaderFavoriteRecord(
            owner_username=username,
            article_id=article_id,
            created_at=_days_ago(days_ago),
        ))
        session.commit()


def seed_assignment(db_sink, article_id, tag, *, is_primary=False, relevance=0.5):
    now = _today_iso()
    with Session(db_sink.engine) as session:
        session.add(ArticleTagAssignmentRecord(
            article_id=article_id,
            tag_id=int(tag.id),
            tag_kind=tag.kind,
            is_primary=is_primary,
            relevance=relevance,
            created_at=now,
            updated_at=now,
        ))
        session.commit()


def seed_read(db_sink, username, source_id, *, reads, days_ago=0):
    with Session(db_sink.engine) as session:
        session.add(ReaderReadRecord(
            day=(datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat(),
            username=username,
            source_id=source_id,
            reads=reads,
            updated_at=_today_iso(),
        ))
        session.commit()


def seed_subscription(db_sink, username, name, *, is_active=True):
    with Session(db_sink.engine) as session:
        session.add(ReaderSubscriptionRecord(
            owner_username=username,
            name=name,
            token_hash=f"hash:{username}:{name}",
            is_active=is_active,
            created_at=_today_iso(),
            updated_at=_today_iso(),
        ))
        session.commit()


def seed_full_user(db_sink, username):
    """一个四段俱全的用户:2 兴趣、2 收藏(带标签)、2 常读来源、2 订阅。

    同一 sink 里给多个用户播种时,cms_tags 的 (kind, normalized_name) 唯一约束
    会拦下重名标签——这里按用户名加后缀,保证 normalized_name 全局唯一。
    """
    with Session(db_sink.engine) as session:
        t1 = seed_cms_tag(session, f"大模型·{username}", code=f"llm-{username}")
        t2 = seed_cms_tag(session, f"具身智能·{username}", code=f"embodied-{username}")
    seed_article(db_sink, f"{username}-a1", f"{username} 的收藏一")
    seed_article(db_sink, f"{username}-a2", f"{username} 的收藏二")
    seed_interest(db_sink, username, t1)
    seed_interest(db_sink, username, t2)
    seed_assignment(db_sink, f"{username}-a1", t1, is_primary=True)
    seed_assignment(db_sink, f"{username}-a2", t2)
    seed_favorite(db_sink, username, f"{username}-a1")
    seed_favorite(db_sink, username, f"{username}-a2", days_ago=1)
    seed_read(db_sink, username, "src_x", reads=5)
    seed_read(db_sink, username, "src_y", reads=3)
    seed_subscription(db_sink, username, f"{username} 的订阅")
    seed_subscription(db_sink, username, f"{username} 的备用订阅")
    return t1, t2


# ── 每段内容 ──────────────────────────────────────────────────────────────────

def test_segments_content():
    db = make_db_sink()
    seed_full_user(db, "alice")
    result = _get_user_context_impl(db, "alice", **DEFAULTS)
    assert result["interest_tags"] == ["大模型·alice", "具身智能·alice"]
    titles = [f["title"] for f in result["recent_favorites"]]
    # 按收藏时间倒序:今天的在前。
    assert titles == ["alice 的收藏一", "alice 的收藏二"]
    assert [f["tags"] for f in result["recent_favorites"]][0] == ["大模型·alice"]
    assert result["top_sources"][0]["source"] == "src_x"
    assert result["top_sources"][0]["reads"] == 5
    assert [s["reads"] for s in result["top_sources"]] == [5, 3]
    assert result["active_subscriptions"] == ["alice 的订阅", "alice 的备用订阅"]
    assert isinstance(result["token_estimate"], int)


def test_interest_only_active_tags_counted():
    db = make_db_sink()
    with Session(db.engine) as session:
        active = seed_cms_tag(session, "活跃标签")
        retired = seed_cms_tag(session, "废弃标签", status="deprecated")
    seed_interest(db, "alice", active)
    seed_interest(db, "alice", retired)
    result = _get_user_context_impl(db, "alice", **DEFAULTS)
    assert result["interest_tags"] == ["活跃标签"]


# ── 条目上限 ─────────────────────────────────────────────────────────────────

def test_interest_tag_limit_truncates():
    db = make_db_sink()
    with Session(db.engine) as session:
        tags = [seed_cms_tag(session, f"标签{i}", code=f"tag{i}") for i in range(3)]
    for tag in tags:
        seed_interest(db, "alice", tag)
    result = _get_user_context_impl(db, "alice", interest_tag_limit=2, favorite_limit=0,
                                    source_limit=0, subscription_limit=0, max_tokens=8000)
    assert result["interest_tags"] == ["标签0", "标签1"]


def test_favorite_limit_truncates():
    db = make_db_sink()
    for i in range(3):
        seed_article(db, f"a{i}", f"文章{i}")
        seed_favorite(db, "alice", f"a{i}", days_ago=i)
    result = _get_user_context_impl(db, "alice", interest_tag_limit=0, favorite_limit=2,
                                    source_limit=0, subscription_limit=0, max_tokens=8000)
    # 收藏时间倒序取前 2。
    assert [f["title"] for f in result["recent_favorites"]] == ["文章0", "文章1"]


def test_source_limit_truncates():
    db = make_db_sink()
    seed_read(db, "alice", "src_a", reads=1)
    seed_read(db, "alice", "src_b", reads=9)
    seed_read(db, "alice", "src_c", reads=5)
    result = _get_user_context_impl(db, "alice", interest_tag_limit=0, favorite_limit=0,
                                    source_limit=2, subscription_limit=0, max_tokens=8000)
    assert [s["source"] for s in result["top_sources"]] == ["src_b", "src_c"]


def test_subscription_limit_truncates():
    db = make_db_sink()
    for i in range(3):
        seed_subscription(db, "alice", f"订阅{i}")
    result = _get_user_context_impl(db, "alice", interest_tag_limit=0, favorite_limit=0,
                                    source_limit=0, subscription_limit=2, max_tokens=8000)
    assert result["active_subscriptions"] == ["订阅0", "订阅1"]


def test_inactive_subscription_excluded():
    db = make_db_sink()
    seed_subscription(db, "alice", "启用订阅", is_active=True)
    seed_subscription(db, "alice", "停用订阅", is_active=False)
    result = _get_user_context_impl(db, "alice", interest_tag_limit=0, favorite_limit=0,
                                    source_limit=0, subscription_limit=10, max_tokens=8000)
    assert result["active_subscriptions"] == ["启用订阅"]


# ── 时间窗 ───────────────────────────────────────────────────────────────────

def test_favorite_window_excludes_old():
    db = make_db_sink()
    seed_article(db, "new", "新收藏")
    seed_article(db, "old", "旧收藏")
    seed_favorite(db, "alice", "new", days_ago=1)
    seed_favorite(db, "alice", "old", days_ago=30)
    result = _get_user_context_impl(db, "alice", **DEFAULTS)
    assert [f["title"] for f in result["recent_favorites"]] == ["新收藏"]


def test_read_window_excludes_old():
    db = make_db_sink()
    seed_read(db, "alice", "src_recent", reads=7, days_ago=1)
    seed_read(db, "alice", "src_stale", reads=99, days_ago=30)
    result = _get_user_context_impl(db, "alice", **DEFAULTS)
    assert [s["source"] for s in result["top_sources"]] == ["src_recent"]


def test_reads_aggregated_across_days():
    db = make_db_sink()
    seed_read(db, "alice", "src_a", reads=2, days_ago=1)
    seed_read(db, "alice", "src_a", reads=3, days_ago=2)
    result = _get_user_context_impl(db, "alice", **DEFAULTS)
    assert result["top_sources"] == [{"source": "src_a", "reads": 5}]


# ── 整体 token 预算 ──────────────────────────────────────────────────────────

def test_token_budget_drops_low_priority_segments_first():
    db = make_db_sink()
    seed_full_user(db, "alice")
    generous = _get_user_context_impl(db, "alice", **{**DEFAULTS, "max_tokens": 8000})
    assert generous["active_subscriptions"] and generous["interest_tags"]
    tight = _get_user_context_impl(db, "alice", **{**DEFAULTS, "max_tokens": 80})
    # 低优先级段被清空,高优先级段保留。
    assert tight["active_subscriptions"] == []
    assert tight["interest_tags"] == ["大模型·alice", "具身智能·alice"]
    assert tight["token_estimate"] <= 80


def test_token_budget_keeps_highest_priority_segment_when_impossible():
    db = make_db_sink()
    seed_full_user(db, "alice")
    result = _get_user_context_impl(db, "alice", **{**DEFAULTS, "max_tokens": 5})
    # 数学上无法满足预算时,至少保住最高优先级段(兴趣标签),不返回空上下文。
    assert result["interest_tags"] == ["大模型·alice", "具身智能·alice"]
    assert result["recent_favorites"] == []
    assert result["top_sources"] == []
    assert result["active_subscriptions"] == []


# ── 空用户与用户隔离 ─────────────────────────────────────────────────────────

def test_empty_user_returns_empty_segments():
    db = make_db_sink()
    result = _get_user_context_impl(db, "nobody", **DEFAULTS)
    assert result["interest_tags"] == []
    assert result["recent_favorites"] == []
    assert result["top_sources"] == []
    assert result["active_subscriptions"] == []
    assert result["token_estimate"] > 0


def test_missing_username_rejected():
    db = make_db_sink()
    assert "error" in _get_user_context_impl(db, None, **DEFAULTS)
    assert "error" in _get_user_context_impl(db, "", **DEFAULTS)
    assert "error" in _get_user_context_impl(db, "   ", **DEFAULTS)


def test_user_isolation():
    db = make_db_sink()
    seed_full_user(db, "alice")
    seed_full_user(db, "bob")
    alice = _get_user_context_impl(db, "alice", **{**DEFAULTS, "max_tokens": 8000})
    assert all("alice" in title for f in alice["recent_favorites"] for title in [f["title"]])
    assert alice["active_subscriptions"] == ["alice 的订阅", "alice 的备用订阅"]
    bob = _get_user_context_impl(db, "bob", **{**DEFAULTS, "max_tokens": 8000})
    assert all("bob" in f["title"] for f in bob["recent_favorites"])
    assert bob["active_subscriptions"] == ["bob 的订阅", "bob 的备用订阅"]


def test_zero_limit_disables_segment():
    db = make_db_sink()
    seed_full_user(db, "alice")
    result = _get_user_context_impl(db, "alice", **{**DEFAULTS, "interest_tag_limit": 0,
                                                    "max_tokens": 8000})
    assert result["interest_tags"] == []
    assert result["recent_favorites"], "未禁用的段照常返回"


# ── 工具层:令牌门控与归属解析 ────────────────────────────────────────────────

def _registered_tool_functions(mcp_app):
    from mcp.server.fastmcp.tools.base import Tool
    tools = mcp_app._tool_manager.list_tools()
    return {t.name for t in tools}


def test_get_user_context_registered_on_server():
    mcp = build_mcp_app(DatabaseStorage(db_url="sqlite:///:memory:"))
    assert "get_user_context" in _registered_tool_functions(mcp)
    # 既有五件套不因新增而丢失。
    assert {"list_sources", "browse_articles", "get_article",
            "search_articles", "get_rag_context"} <= _registered_tool_functions(mcp)


def test_tool_missing_token_rejected():
    import asyncio
    mcp = build_mcp_app(
        DatabaseStorage(db_url="sqlite:///:memory:"),
        token_owner_resolver=lambda token: "alice" if token == "good" else None,
    )
    result = asyncio.run(mcp.call_tool("get_user_context", {}))
    assert "dfeed_" in str(result)


def test_tool_without_resolver_rejected():
    import asyncio
    mcp = build_mcp_app(DatabaseStorage(db_url="sqlite:///:memory:"))
    result = asyncio.run(mcp.call_tool("get_user_context", {"subscription_token": "dfeed_any"}))
    assert "dfeed_" in str(result)


def test_tool_resolves_owner_and_scopes_to_token_user():
    import asyncio
    db = make_db_sink()
    seed_full_user(db, "alice")
    seed_full_user(db, "bob")
    mcp = build_mcp_app(
        db,
        token_owner_resolver=lambda token: "alice" if token == "alice-token" else None,
        user_context_limits={**DEFAULTS, "max_tokens": 8000},
    )
    result = asyncio.run(mcp.call_tool("get_user_context", {"subscription_token": "alice-token"}))
    # call_tool 返回结构在不同版本有差异,统一转字符串后断言关键内容:
    # 只含 alice 的数据,不含 bob 的。
    text = str(result)
    assert "alice 的订阅" in text
    assert "bob" not in text
