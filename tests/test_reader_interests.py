"""兴趣即透镜(issue #27 第一波):文章列表的三谓词面板 订阅 / 兴趣 / 收藏 与命中标注。

订阅、兴趣、收藏是三个两两正交的谓词,`GET /api/articles` 按 AND 联合;三者全关 = 全站可见源。
兴趣谓词只认「主标签 或 相关度 ≥ 门槛」的指派(它决定全站范围内「放什么进来」);屏蔽在列表里
不是硬排除——条目照常返回并带 interest_muted,由前端折成一行。
"""
import datetime
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from services.reader_interests import INTEREST_MATCH_MIN_RELEVANCE  # noqa: E402
from services.reader_state import UNCURSORED_UNREAD_MAX_AGE_DAYS  # noqa: E402

STAMP = "2026-09-09T00:00:00"


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _seed_users(engine):
    from services import accounts as accounts_service
    from models.db import UserRecord

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in (("admin", "admin", "admin"), ("user", "user", "user")):
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password(password),
                role=role,
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
        session.commit()


def _seed_article(engine, article_id: str, source_id: str, *, fetched: str = ""):
    """默认昨天入库:无水位源的未读有 30 天时效下限,固定的历史日期会让样本一律视为已读。"""
    from models.db import ArticleRecord

    fetched = fetched or (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat()

    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id,
            title=f"Title {article_id}",
            content_type="web_article",
            source_id=source_id,
            source_url=f"https://example.test/{article_id}",
            publish_date="2026-05-20T00:00:00",
            fetched_date=fetched,
            has_content=True,
            content=f"{article_id} body",
            extensions_json="{}",
        ))
        session.commit()


def _seed_tag(engine, code: str, name_zh: str) -> int:
    from models.db import CmsTagRecord

    with Session(engine) as session:
        tag = CmsTagRecord(
            code=code, kind="topic", name_zh=name_zh, name_en=code, normalized_name=code,
            status="active", user_selectable=True, created_at=STAMP, updated_at=STAMP,
        )
        session.add(tag)
        session.commit()
        return int(tag.id)


def _assign(engine, article_id: str, tag_id: int, *, primary: bool = False, relevance: float = 0.9):
    from models.db import ArticleTagAssignmentRecord

    with Session(engine) as session:
        session.add(ArticleTagAssignmentRecord(
            article_id=article_id, tag_id=tag_id, tag_kind="topic", is_primary=primary,
            relevance=relevance, assignment_source="llm", prompt_version="t", taxonomy_version=1,
            created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()


def _set_interest(engine, username: str, tag_id: int, stance: str):
    from models.db import UserInterestTagRecord

    with Session(engine) as session:
        session.add(UserInterestTagRecord(
            owner_username=username, tag_id=tag_id, stance=stance, priority="normal",
            source="explicit", created_at=STAMP, updated_at=STAMP,
        ))
        session.commit()


def _make_app(monkeypatch, tmp_path, name: str):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage
    from services import daily_brief as daily_brief_service

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    # 预标记「默认订阅已播种」,否则登录点会把精选名单订上、破坏范围断言
    with Session(sink.engine) as session:
        daily_brief_service.set_setting(session, f"{app_module.DEFAULTS_SEEDED_KEY_PREFIX}:user", "preseeded")
    return app_module, sink


def _ids(client, **params):
    base = {"include_total": "true", "include_content": "false", "shape": "article"}
    data = client.get("/api/articles", params={**base, **params}).json()
    return {item["id"] for item in data["items"]}, data


def _setup(monkeypatch, tmp_path):
    """两个源(sub 已订阅 / other 未订阅),两枚标签(agents 关注 / robots 屏蔽),五篇文章。"""
    app_module, sink = _make_app(monkeypatch, tmp_path, "interest.db")
    e = sink.engine
    agents = _seed_tag(e, "ai-agents", "AI 智能体")
    robots = _seed_tag(e, "robotics", "机器人技术")
    _seed_article(e, "sub_hit", "web_anthropic_news")       # 订阅源 · 主标签命中关注
    _seed_article(e, "sub_weak", "web_anthropic_news")      # 订阅源 · 低相关度命中(不过门槛)
    _seed_article(e, "sub_plain", "web_anthropic_news")     # 订阅源 · 无标签
    _seed_article(e, "sub_muted", "web_anthropic_news")     # 订阅源 · 命中屏蔽 + 关注
    _seed_article(e, "out_hit", "web_qbitai")               # 未订阅源 · 高相关度命中关注
    _assign(e, "sub_hit", agents, primary=True, relevance=0.95)
    _assign(e, "sub_weak", agents, primary=False, relevance=INTEREST_MATCH_MIN_RELEVANCE - 0.2)
    _assign(e, "sub_muted", robots, primary=True, relevance=0.9)
    _assign(e, "sub_muted", agents, primary=False, relevance=0.9)
    _assign(e, "out_hit", agents, primary=False, relevance=INTEREST_MATCH_MIN_RELEVANCE)
    _set_interest(e, "user", agents, "follow")
    _set_interest(e, "user", robots, "mute")
    return app_module, sink


def test_interest_scope_composes_with_subscribed_scope(monkeypatch, tmp_path):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/web_anthropic_news/subscribe")
        # 订阅 ∧ 兴趣:订阅源里过门槛的命中(低相关度不算;屏蔽项照常返回,由前端折叠)
        ids, _ = _ids(client, subscribed_scope="only", interest_scope="only")
        assert ids == {"sub_hit", "sub_muted"}
        # 兴趣 · 全站(订阅关掉):未订阅源的命中进来
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only")
        assert ids == {"sub_hit", "sub_muted", "out_hit"}
        # 全关 = 全站可见源
        ids, _ = _ids(client)
        assert ids == {"sub_hit", "sub_weak", "sub_plain", "sub_muted", "out_hit"}
        # 只订阅:与今天的「全部文章」同义
        ids, _ = _ids(client, subscribed_scope="only")
        assert ids == {"sub_hit", "sub_weak", "sub_plain", "sub_muted"}


def test_interest_tag_id_narrows_to_one_followed_tag(monkeypatch, tmp_path):
    """兴趣轴下钻(五稿):interest_tag_id 只看命中这一个关注标签的;非关注标签 id 显式空集。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    e = sink.engine
    llm = _seed_tag(e, "llm", "大语言模型")
    _seed_article(e, "out_llm", "web_qbitai")
    _assign(e, "out_llm", llm, primary=True, relevance=0.9)
    _set_interest(e, "user", llm, "follow")
    with Session(e) as session:
        from models.db import CmsTagRecord
        agents_id = session.exec(select(CmsTagRecord.id).where(CmsTagRecord.code == "ai-agents")).one()
        robots_id = session.exec(select(CmsTagRecord.id).where(CmsTagRecord.code == "robotics")).one()
    with TestClient(app_module.app) as client:
        _login(client)
        # 兴趣全集(全站):两枚关注标签的命中并集
        ids, _ = _ids(client, interest_scope="only")
        assert ids == {"sub_hit", "sub_muted", "out_hit", "out_llm"}
        # 下钻到「AI 智能体」:大语言模型的命中不在
        ids, data = _ids(client, interest_scope="only", interest_tag_id=agents_id)
        assert ids == {"sub_hit", "sub_muted", "out_hit"}
        assert data["total"] == 3
        # 下钻到「大语言模型」
        ids, _ = _ids(client, interest_scope="only", interest_tag_id=llm)
        assert ids == {"out_llm"}
        # 屏蔽标签不是关注标签:按它下钻是显式空集,不退化成全部兴趣
        ids, data = _ids(client, interest_scope="only", interest_tag_id=robots_id)
        assert ids == set() and data["total"] == 0
        # 不带 interest_scope=only 时 interest_tag_id 无效(单独的 tag id 检索走 tag_ids)
        ids, _ = _ids(client, interest_tag_id=agents_id)
        assert "sub_plain" in ids


def test_interest_scope_without_interests_is_explicit_empty(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "nointerest.db")
    _seed_article(sink.engine, "a1", "web_anthropic_news")
    with TestClient(app_module.app) as client:
        _login(client)
        ids, data = _ids(client, interest_scope="only")
        assert ids == set()
        assert data["total"] == 0


def test_favorite_scope_and_total_pairing(monkeypatch, tmp_path):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/web_anthropic_news/subscribe")
        assert client.post("/api/reader/favorites/sub_plain").status_code == 200
        assert client.post("/api/reader/favorites/out_hit").status_code == 200
        ids, data = _ids(client, favorite_scope="only")
        assert ids == {"sub_plain", "out_hit"}
        assert data["total"] == 2  # count_query 与 query 成对加条件
        # 订阅 ∧ 收藏 / 兴趣 ∧ 收藏
        ids, _ = _ids(client, subscribed_scope="only", favorite_scope="only")
        assert ids == {"sub_plain"}
        ids, _ = _ids(client, interest_scope="only", favorite_scope="only")
        assert ids == {"out_hit"}


def test_with_interest_annotates_hits_and_muted(monkeypatch, tmp_path):
    app_module, _sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        _, data = _ids(client, with_interest="true")
        by_id = {item["id"]: item for item in data["items"]}
        assert by_id["sub_hit"]["interest_hits"] == ["AI 智能体"]
        assert by_id["sub_hit"]["interest_muted"] == []
        assert by_id["sub_weak"]["interest_hits"] == []          # 低相关度不算命中
        assert by_id["sub_plain"]["interest_hits"] == []          # 未打标 = 未知,不是不命中
        assert by_id["sub_muted"]["interest_muted"] == ["机器人技术"]
        assert by_id["sub_muted"]["interest_hits"] == ["AI 智能体"]
        # 不带 with_interest 时不标注(形状不变)
        _, plain = _ids(client)
        assert "interest_hits" not in plain["items"][0]


def test_unread_only_in_site_scope_uses_per_article_read_state(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/web_anthropic_news/subscribe")
        client.get("/api/reader/unread-counts")  # 校准订阅源水位
        # 读掉一篇未订阅源的文章
        assert client.post("/api/reader/articles/out_hit/read").status_code == 200
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only", unread_only="true")
        assert "out_hit" not in ids       # 无水位源按逐篇读态判定:读过即已读
        assert "sub_hit" in ids            # 订阅源按水位:未读
        # 只订阅 + 未读:口径不变(订阅外文章不进来)
        ids, _ = _ids(client, subscribed_scope="only", unread_only="true")
        assert "out_hit" not in ids
        assert "sub_hit" in ids
        # 页级 unread 标注与过滤同尺子(codex 检视 P1):全站范围里无水位源的条目
        # 没读过即 unread=true、读过即 false;只订阅范围不受影响
        _seed_article(sink.engine, "out_fresh", "web_qbitai")
        with Session(sink.engine) as session:
            from models.db import CmsTagRecord
            agents_id = session.exec(select(CmsTagRecord.id).where(CmsTagRecord.code == "ai-agents")).one()
        _assign(sink.engine, "out_fresh", agents_id, primary=True, relevance=0.9)
        _, data = _ids(client, subscribed_scope="off", interest_scope="only", with_unread="true")
        flags = {item["id"]: item["unread"] for item in data["items"]}
        assert flags["out_fresh"] is True and flags["out_hit"] is False and flags["sub_hit"] is True
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only", unread_only="true")
        assert "out_fresh" in ids and "out_hit" not in ids


def _days_ago(days: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()


def test_uncursored_unread_has_age_floor(monkeypatch, tmp_path):
    """无水位源的未读时效下限:入库超过 UNCURSORED_UNREAD_MAX_AGE_DAYS 的命中一律视为已读(过滤与标注同尺)。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    e = sink.engine
    with Session(e) as session:
        from models.db import CmsTagRecord
        agents_id = session.exec(select(CmsTagRecord.id).where(CmsTagRecord.code == "ai-agents")).one()
    _seed_article(e, "out_recent", "web_qbitai", fetched=_days_ago(3))
    _seed_article(e, "out_stale", "web_qbitai", fetched=_days_ago(UNCURSORED_UNREAD_MAX_AGE_DAYS + 5))
    _assign(e, "out_recent", agents_id, primary=True, relevance=0.9)
    _assign(e, "out_stale", agents_id, primary=True, relevance=0.9)
    with TestClient(app_module.app) as client:
        _login(client)
        ids, data = _ids(client, subscribed_scope="off", interest_scope="only", with_unread="true")
        flags = {item["id"]: item["unread"] for item in data["items"]}
        assert flags["out_recent"] is True and flags["out_stale"] is False
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only", unread_only="true")
        assert "out_recent" in ids and "out_stale" not in ids
        # 显式标未读压过时效下限(读者的意图优先)
        assert client.post("/api/reader/articles/out_stale/mark-unread").status_code == 200
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only", unread_only="true")
        assert "out_stale" in ids


def test_mark_scope_read_marks_interest_hits_per_article(monkeypatch, tmp_path):
    """兴趣轴「全部标读」:按范围逐篇写读态,不推水位;单标签范围只标该标签的命中。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    e = sink.engine
    llm = _seed_tag(e, "llm", "大语言模型")
    _seed_article(e, "out_llm", "web_qbitai", fetched=_days_ago(2))
    _assign(e, "out_llm", llm, primary=True, relevance=0.9)
    _set_interest(e, "user", llm, "follow")
    with Session(e) as session:
        from models.db import CmsTagRecord
        agents_id = session.exec(select(CmsTagRecord.id).where(CmsTagRecord.code == "ai-agents")).one()
    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/web_anthropic_news/subscribe")
        client.get("/api/reader/unread-counts")
        # 先只标「大语言模型」:AI 智能体的命中仍未读
        res = client.post("/api/reader/mark-scope-read", params={"shape": "article", "interest_tag_id": agents_id + llm + 1000})
        assert res.status_code == 200 and res.json()["marked"] == 0  # 非关注标签 id:显式空集
        res = client.post("/api/reader/mark-scope-read", params={"shape": "article", "interest_tag_id": llm})
        assert res.status_code == 200 and res.json()["marked"] == 1
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only", unread_only="true")
        assert "out_llm" not in ids and "out_hit" in ids and "sub_hit" in ids
        # 兴趣全集标读:订阅源的命中也按篇写行,该源里未命中兴趣的 sub_plain 不受影响(不推水位)
        res = client.post("/api/reader/mark-scope-read", params={"shape": "article"})
        body = res.json()
        assert res.status_code == 200 and body["marked"] >= 2 and "by_source" in body
        ids, _ = _ids(client, subscribed_scope="off", interest_scope="only", unread_only="true")
        assert ids == set()
        ids, _ = _ids(client, subscribed_scope="only", unread_only="true")
        assert "sub_plain" in ids and "sub_hit" not in ids
