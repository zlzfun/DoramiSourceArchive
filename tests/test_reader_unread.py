"""未读体系测试(迭代 1 · B):read 双写、水位语义、unread-counts、全部标读、
unread_only 过滤、with_unread 页级标记与单篇手动标读/标未读。

水位语义要点(backlog 初始化,Folo 式):
- 订阅成功/存量订阅懒初始化 → 水位 = 该源第 K+1 新文章的 fetched_date,
  最近 K(=INIT_UNREAD_BACKLOG)篇成为未读积压;不足 K+1 篇则全部未读;
- 退订清水位,再订阅重新起算 backlog;
- 全部标读推进水位到当下并清掉被覆盖的逐篇状态行;
- 逐篇显式覆盖优先于水位:is_read=False(手动标未读)即使被水位盖过也算未读。
"""
import os
import sys
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


_DEFAULT_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"))

# fetched_date 恒晚/恒早于「当下」的哨兵时刻,使水位比较确定化
FUTURE_FETCH = "2099-01-01T00:00:00"
PAST_FETCH = "2000-01-01T00:00:00"


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


def _seed_users(engine, accounts=_DEFAULT_ACCOUNTS):
    import datetime
    from services import accounts as accounts_service
    from models.db import UserRecord

    now = datetime.datetime.now().isoformat()
    with Session(engine) as session:
        for username, password, role in accounts:
            existing = session.get(UserRecord, username)
            if existing is not None:
                session.delete(existing)
                session.commit()
            session.add(UserRecord(
                username=username,
                password_hash=accounts_service.hash_password(password),
                role=role,
                is_active=True,
                created_at=now,
                updated_at=now,
            ))
        session.commit()


def _seed_article(engine, article_id: str, source_id: str, fetched_date: str = FUTURE_FETCH):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(
            ArticleRecord(
                id=article_id,
                title=f"Title {article_id}",
                content_type="rss_article",
                source_id=source_id,
                source_url=f"https://example.test/{article_id}",
                publish_date="2026-05-20T00:00:00",
                fetched_date=fetched_date,
                has_content=True,
                content=f"{article_id} body",
                extensions_json="{}",
                is_vectorized=False,
            )
        )
        session.commit()


def _make_app(monkeypatch, tmp_path, name: str):
    import api.app as app_module
    from config import RuntimeConfig
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine)
    monkeypatch.setattr(
        app_module, "settings", replace(app_module.settings, runtime=RuntimeConfig(role="all"))
    )
    return app_module, sink


def _unread(client: TestClient):
    response = client.get("/api/reader/unread-counts")
    assert response.status_code == 200
    return response.json()


# ==================== read 双写 ====================

def test_read_endpoint_dual_writes_state_and_metering(monkeypatch, tmp_path):
    from models.db import ReaderArticleReadStateRecord, ReaderReadRecord

    app_module, sink = _make_app(monkeypatch, tmp_path, "dual.db")
    _seed_article(sink.engine, "a1", "src_a")

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/articles/a1/read").json()["status"] == "ok"

    with Session(sink.engine) as session:
        state = session.get(ReaderArticleReadStateRecord, ("user", "a1"))
        assert state is not None and state.read_at and state.is_read is True
        metering = session.exec(
            select(ReaderReadRecord).where(ReaderReadRecord.username == "user")
        ).first()
        assert metering is not None and metering.reads == 1 and metering.source_id == "src_a"

    # 文章不存在:安静忽略,两表都不写
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/articles/nope/read").json()["status"] == "ignored"


# ==================== 水位语义 ====================

def _seed_backlog(engine, source_id: str, count: int, prefix: str = "old"):
    """按分钟递增的历史 fetched_date 批量播种,便于断言 backlog 截断。"""
    for i in range(count):
        _seed_article(
            engine, f"{prefix}{i}", source_id,
            fetched_date=f"2000-01-01T00:{i:02d}:00",
        )


def test_subscribe_keeps_recent_backlog_unread(monkeypatch, tmp_path):
    """订阅初始化水位为 backlog 语义:最近 K 篇成为未读积压(不再是一片已读)。"""
    from services.reader_state import INIT_UNREAD_BACKLOG

    app_module, sink = _make_app(monkeypatch, tmp_path, "watermark.db")
    _seed_backlog(sink.engine, "src_a", INIT_UNREAD_BACKLOG + 5)

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/sources/src_a/subscribe").status_code == 200
        # 25 篇存量 → 最近 20 篇未读,更早 5 篇视为已读
        data = _unread(client)
        assert data["by_source"] == {"src_a": INIT_UNREAD_BACKLOG}
        # 新到文章照常叠加
        _seed_article(sink.engine, "new1", "src_a", fetched_date=FUTURE_FETCH)
        assert _unread(client)["total"] == INIT_UNREAD_BACKLOG + 1


def test_subscribe_small_source_all_unread(monkeypatch, tmp_path):
    """源不足 K+1 篇时空水位:全部未读(小源不截断)。"""
    app_module, sink = _make_app(monkeypatch, tmp_path, "small.db")
    _seed_article(sink.engine, "old1", "src_a", fetched_date=PAST_FETCH)

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        assert _unread(client)["by_source"] == {"src_a": 1}


def test_legacy_subscription_lazy_inits_backlog(monkeypatch, tmp_path):
    """经 /api/subscriptions 高级路径创建的订阅没有水位行——首访未读应为 backlog
    截断后的最近 K 篇(升级后的存量订阅同理),而非 0 或全量。"""
    from services.reader_state import INIT_UNREAD_BACKLOG

    app_module, sink = _make_app(monkeypatch, tmp_path, "legacy.db")
    _seed_backlog(sink.engine, "src_a", INIT_UNREAD_BACKLOG + 5)

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.post(
            "/api/subscriptions",
            json={"name": "legacy", "filters": {"source_id": "src_a"}},
        )
        assert response.status_code == 200
        assert _unread(client)["total"] == INIT_UNREAD_BACKLOG


def test_open_article_clears_unread(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "open.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        _seed_article(sink.engine, "n1", "src_a")
        _seed_article(sink.engine, "n2", "src_a")
        assert _unread(client)["total"] == 2
        client.post("/api/reader/articles/n1/read")
        data = _unread(client)
        assert data["total"] == 1 and data["by_source"] == {"src_a": 1}


def test_resubscribe_restarts_backlog(monkeypatch, tmp_path):
    import datetime

    app_module, sink = _make_app(monkeypatch, tmp_path, "resub.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        _seed_article(sink.engine, "n1", "src_a", fetched_date=datetime.datetime.now().isoformat())
        assert _unread(client)["total"] == 1
        # 退订清水位 → 不再计入
        client.delete("/api/reader/sources/src_a/subscribe")
        assert _unread(client)["total"] == 0
        # 再订阅重新起算 backlog:小源全部回到未读(n1 复现)
        client.post("/api/reader/sources/src_a/subscribe")
        assert _unread(client)["total"] == 1
        # 全部标读后归零
        assert client.post("/api/reader/mark-all-read").json()["total"] == 0


def test_unread_isolated_between_users(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "isolate.db")

    with TestClient(app_module.app) as client:
        _login(client)  # user
        client.post("/api/reader/sources/src_a/subscribe")
        _seed_article(sink.engine, "n1", "src_a")
        client.post("/api/reader/articles/n1/read")
        assert _unread(client)["total"] == 0

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        client.post("/api/reader/sources/src_a/subscribe")
        _seed_article(sink.engine, "n2", "src_a")
        # admin 自己的水位/已读独立:只有 n2 未读(n1 早于 admin 的订阅时刻? 均为 FUTURE)
        # n1、n2 的 fetched_date 都是 FUTURE → admin 订阅水位(当下)之后 → 都未读;
        # 但 user 读过 n1 不影响 admin。
        assert _unread(client)["total"] == 2


# ==================== 全部标读 ====================

def test_mark_all_read_single_source_and_global(monkeypatch, tmp_path):
    import datetime

    from models.db import ReaderArticleReadStateRecord

    def _now():
        return datetime.datetime.now().isoformat()

    app_module, sink = _make_app(monkeypatch, tmp_path, "markall.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        client.post("/api/reader/sources/src_b/subscribe")
        _seed_article(sink.engine, "a1", "src_a", fetched_date=_now())
        _seed_article(sink.engine, "b1", "src_b", fetched_date=_now())
        client.post("/api/reader/articles/a1/read")  # 留下一条逐篇已读行
        assert _unread(client)["total"] == 1  # b1

        # 单源标读:src_b 清零,响应即为最新统计;src_a 的 a1 已读行不受影响
        data = client.post("/api/reader/sources/src_b/mark-all-read").json()
        assert data["status"] == "success" and data["total"] == 0
        with Session(sink.engine) as session:
            rows = session.exec(select(ReaderArticleReadStateRecord)).all()
            assert {(r.owner_username, r.article_id) for r in rows} == {("user", "a1")}

        # 全局标读:未读清零,且被水位覆盖的存量已读行全部清理(防膨胀)
        _seed_article(sink.engine, "a2", "src_a", fetched_date=_now())
        assert _unread(client)["total"] == 1
        data = client.post("/api/reader/mark-all-read").json()
        assert data["total"] == 0
        with Session(sink.engine) as session:
            assert session.exec(select(ReaderArticleReadStateRecord)).all() == []


def test_mark_all_read_shape_scoped(monkeypatch, tmp_path):
    """容器级全部标读(阅读器容器化):shape=article|bulletin 只推进本容器源的水位。

    src_a 未注册 → source_shape 兜底 article;github_trending_daily 是注册表动态形源。
    """
    import datetime

    app_module, sink = _make_app(monkeypatch, tmp_path, "markall_shape.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        client.post("/api/reader/sources/github_trending_daily/subscribe")
        now = datetime.datetime.now().isoformat()
        _seed_article(sink.engine, "a1", "src_a", fetched_date=now)
        _seed_article(sink.engine, "t1", "github_trending_daily", fetched_date=now)
        assert _unread(client)["total"] == 2

        # 只标文章容器:动态源的未读原样保留
        data = client.post("/api/reader/mark-all-read?shape=article").json()
        assert data["by_source"].get("src_a", 0) == 0
        assert data["by_source"].get("github_trending_daily", 0) == 1

        # 再标动态容器 → 全部归零
        assert client.post("/api/reader/mark-all-read?shape=bulletin").json()["total"] == 0


# ==================== unread_only 过滤 / with_unread 标记 ====================

def test_unread_only_filter_and_with_unread_flag(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "filter.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        # 用「全部标读」把水位固定到当下,使 PAST/FUTURE 哨兵与水位的关系确定化
        client.post("/api/reader/sources/src_a/mark-all-read")
        _seed_article(sink.engine, "old1", "src_a", fetched_date=PAST_FETCH)
        _seed_article(sink.engine, "n1", "src_a")
        _seed_article(sink.engine, "n2", "src_a")

        # unread_only:只回水位后的未读两篇
        response = client.get(
            "/api/articles",
            params={"subscribed_scope": "only", "unread_only": "true",
                    "with_unread": "true", "include_total": "true", "include_content": "false"},
        )
        assert response.status_code == 200
        data = response.json()
        assert {item["id"] for item in data["items"]} == {"n1", "n2"}
        assert all(item["unread"] is True for item in data["items"])
        assert data["total"] == 2

        # 读掉 n1 → unread_only 少一篇;全量列表里 n1 的标记翻为 False
        client.post("/api/reader/articles/n1/read")
        response = client.get(
            "/api/articles",
            params={"subscribed_scope": "only", "unread_only": "true",
                    "include_total": "true", "include_content": "false"},
        )
        assert {item["id"] for item in response.json()["items"]} == {"n2"}

        response = client.get(
            "/api/articles",
            params={"subscribed_scope": "only", "with_unread": "true",
                    "include_total": "true", "include_content": "false"},
        )
        flags = {item["id"]: item["unread"] for item in response.json()["items"]}
        assert flags == {"old1": False, "n1": False, "n2": True}


def test_unread_only_without_subscription_returns_empty(monkeypatch, tmp_path):
    app_module, sink = _make_app(monkeypatch, tmp_path, "nosub.db")
    _seed_article(sink.engine, "n1", "src_a")

    with TestClient(app_module.app) as client:
        _login(client)
        response = client.get(
            "/api/articles",
            params={"unread_only": "true", "include_total": "true", "include_content": "false"},
        )
        assert response.status_code == 200
        assert response.json()["items"] == []


# ==================== 单篇手动标读 / 标未读 ====================

def test_manual_mark_unread_undoes_open(monkeypatch, tmp_path):
    """打开文章误触已读后,「标为未读」可撤销——显式覆盖优先于水位。"""
    app_module, sink = _make_app(monkeypatch, tmp_path, "manual.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        _seed_article(sink.engine, "n1", "src_a")
        assert _unread(client)["total"] == 1
        client.post("/api/reader/articles/n1/read")  # 打开即读
        assert _unread(client)["total"] == 0
        # 标回未读:计数与列表标记都复原
        response = client.post("/api/reader/articles/n1/mark-unread")
        assert response.status_code == 200 and response.json()["is_read"] is False
        assert _unread(client)["total"] == 1
        listing = client.get(
            "/api/articles",
            params={"subscribed_scope": "only", "unread_only": "true",
                    "with_unread": "true", "include_total": "true", "include_content": "false"},
        ).json()
        assert [item["id"] for item in listing["items"]] == ["n1"]
        assert listing["items"][0]["unread"] is True
        # 再手动标已读:归零
        assert client.post("/api/reader/articles/n1/mark-read").json()["is_read"] is True
        assert _unread(client)["total"] == 0


def test_manual_mark_unread_overrides_watermark(monkeypatch, tmp_path):
    """被水位覆盖(视为已读)的历史文章,手动标未读同样生效。"""
    app_module, sink = _make_app(monkeypatch, tmp_path, "override.db")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/src_a/subscribe")
        client.post("/api/reader/sources/src_a/mark-all-read")  # 水位推到当下
        _seed_article(sink.engine, "old1", "src_a", fetched_date=PAST_FETCH)
        assert _unread(client)["total"] == 0  # 水位覆盖,已读
        client.post("/api/reader/articles/old1/mark-unread")
        data = _unread(client)
        assert data["total"] == 1 and data["by_source"] == {"src_a": 1}
        # 「全部标读」把显式未读也一并覆盖清除
        assert client.post("/api/reader/mark-all-read").json()["total"] == 0


def test_manual_mark_missing_article_404_and_no_metering(monkeypatch, tmp_path):
    """手动标读走 404(非静默),且不产生阅读计量。"""
    from models.db import ReaderReadRecord

    app_module, sink = _make_app(monkeypatch, tmp_path, "manual404.db")
    _seed_article(sink.engine, "n1", "src_a")

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.post("/api/reader/articles/nope/mark-read").status_code == 404
        client.post("/api/reader/articles/n1/mark-read")

    with Session(sink.engine) as session:
        assert session.exec(select(ReaderReadRecord)).all() == []  # 无计量行


# ==================== 门禁 ====================

def test_unread_endpoints_require_login(monkeypatch, tmp_path):
    app_module, _sink = _make_app(monkeypatch, tmp_path, "gate.db")

    with TestClient(app_module.app) as client:
        assert client.get("/api/reader/unread-counts").status_code in (401, 403)
        assert client.post("/api/reader/mark-all-read").status_code in (401, 403)
        assert client.post("/api/reader/articles/x/mark-read").status_code in (401, 403)
        assert client.post("/api/reader/articles/x/mark-unread").status_code in (401, 403)
