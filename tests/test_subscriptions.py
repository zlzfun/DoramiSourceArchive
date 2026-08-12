import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def _login(client: TestClient, username: str = "user", password: str = "user") -> None:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200


_DEFAULT_ACCOUNTS = (("admin", "admin", "admin"), ("user", "user", "user"))


def _seed_users(engine, accounts=_DEFAULT_ACCOUNTS):
    """将测试账户播种进给定引擎的 users 表（账户已迁移到数据库托管）。

    直接落库 UserRecord（等价于首次启动播种），因为业务层已禁止经 create_user
    新建管理员——管理员只能由 seed 路径产生。
    """
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


def _set_auth_accounts(monkeypatch, app_module, accounts=_DEFAULT_ACCOUNTS):
    _seed_users(app_module.db_sink.engine, accounts)


def _mark_defaults_seeded(engine, usernames=("user",)):
    """预标记「默认订阅已播种」，使登录点播种跳过——供关注零订阅初态的测试保持原语义。

    （默认订阅自登录点播种起，凡真实登录都会自带精选订阅；这些测试验证的是
    订阅机制本身，需要一个干净的零订阅账号。）
    """
    from services import daily_brief as daily_brief_service
    import api.app as app_module

    with Session(engine) as session:
        for username in usernames:
            daily_brief_service.set_setting(
                session, f"{app_module.DEFAULTS_SEEDED_KEY_PREFIX}:{username}", "test-preseeded"
            )


def _set_runtime_role(monkeypatch, app_module, role: str):
    from config import RuntimeConfig

    monkeypatch.setattr(
        app_module,
        "settings",
        replace(app_module.settings, runtime=RuntimeConfig(role=role)),
    )


def _seed_article(engine, article_id: str, source_id: str, title: str):
    from models.db import ArticleRecord

    with Session(engine) as session:
        session.add(
            ArticleRecord(
                id=article_id,
                title=title,
                content_type="rss_article",
                source_id=source_id,
                source_url=f"https://example.test/{article_id}",
                publish_date="2026-05-20T00:00:00",
                fetched_date="2026-05-21T00:00:00",
                has_content=True,
                content=f"{title} body",
                extensions_json="{}",
            )
        )
        session.commit()


def _make_sink(tmp_path, name: str):
    from storage.impl.db_storage import DatabaseStorage

    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")


def test_subscription_tokenized_delivery_filters_articles(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "subscriptions.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article(sink.engine, "openai_1", "rss_openai", "OpenAI Update")
    _seed_article(sink.engine, "hf_1", "rss_huggingface", "Hugging Face Update")

    with TestClient(app_module.app) as client:
        _login(client)
        create_response = client.post(
            "/api/subscriptions",
            json={
                "name": "OpenAI feed",
                "filters": {"source_id": "rss_openai"},
                "delivery_policy": {"include_content": True, "default_limit": 50, "max_limit": 100},
            },
        )
        assert create_response.status_code == 200
        subscription = create_response.json()
        assert subscription["token"].startswith("dsub_")
        assert subscription["token_preview"] == f"...{subscription['token'][-6:]}"

        public_response = client.get(
            f"/api/public/subscriptions/{subscription['id']}/articles",
            headers={"Authorization": f"Bearer {subscription['token']}"},
        )
        assert public_response.status_code == 200
        data = public_response.json()
        assert data["count"] == 1
        assert data["items"][0]["id"] == "openai_1"
        assert data["items"][0]["metadata"]["source_id"] == "rss_openai"
        assert "content" in data["items"][0]


def test_subscription_public_delivery_requires_valid_token(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "tokens.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)
        subscription = client.post("/api/subscriptions", json={"name": "Token test"}).json()

        assert client.get(f"/api/public/subscriptions/{subscription['id']}/articles").status_code == 401
        assert client.get(
            f"/api/public/subscriptions/{subscription['id']}/articles?token=wrong"
        ).status_code == 401

        ok = client.get(
            f"/api/public/subscriptions/{subscription['id']}/articles?token={subscription['token']}"
        )
        assert ok.status_code == 200

        missing = client.get("/api/public/subscriptions/999/articles?token=wrong")
        assert missing.status_code == 401


def test_subscription_token_rotation_invalidates_old_token(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "rotation.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)
        created = client.post("/api/subscriptions", json={"name": "Rotating"}).json()
        rotated = client.post(f"/api/subscriptions/{created['id']}/rotate-token").json()

        old_response = client.get(
            f"/api/public/subscriptions/{created['id']}/articles?token={created['token']}"
        )
        new_response = client.get(
            f"/api/public/subscriptions/{created['id']}/articles?token={rotated['token']}"
        )

        assert old_response.status_code == 401
        assert new_response.status_code == 200


def test_subscription_delivery_disabled_in_collector_role(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "collector.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "collector")

    with TestClient(app_module.app) as client:
        response = client.get("/api/public/subscriptions/1/articles?token=anything")
        assert response.status_code == 403


def test_subscriptions_are_isolated_per_user(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "owners.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine, (("alice", "alice", "user"), ("bob", "bob", "user")))
    _mark_defaults_seeded(sink.engine, ("alice", "bob"))
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as alice:
        _login(alice, "alice", "alice")
        created = alice.post("/api/subscriptions", json={"name": "Alice feed"}).json()
        assert len(alice.get("/api/subscriptions").json()) == 1

    with TestClient(app_module.app) as bob:
        _login(bob, "bob", "bob")
        # Bob sees none of Alice's subscriptions.
        assert bob.get("/api/subscriptions").json() == []
        # Bob cannot read, edit, rotate, or delete Alice's subscription.
        assert bob.get(f"/api/subscriptions/{created['id']}").status_code == 404
        assert bob.put(f"/api/subscriptions/{created['id']}", json={"name": "hijack"}).status_code == 404
        assert bob.post(f"/api/subscriptions/{created['id']}/rotate-token").status_code == 404
        assert bob.delete(f"/api/subscriptions/{created['id']}").status_code == 404

    # Alice's token still works for public delivery regardless of owner scoping.
    with TestClient(app_module.app) as public:
        ok = public.get(
            f"/api/public/subscriptions/{created['id']}/articles?token={created['token']}"
        )
        assert ok.status_code == 200


def test_reader_sources_catalog_marks_subscribed(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ArticleRecord

    sink = _make_sink(tmp_path, "catalog.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with Session(sink.engine) as session:
        for i in range(3):
            session.add(ArticleRecord(
                id=f"o{i}", title=f"o{i}", content_type="rss_article", source_id="rss_openai",
                source_url="https://e.test", publish_date="2026-05-20T00:00:00",
                fetched_date=f"2026-05-2{i}T00:00:00", has_content=True, content="x",
                extensions_json="{}",
            ))
        session.add(ArticleRecord(
            id="g1", title="g1", content_type="github_repository", source_id="gh_repo",
            source_url="https://e.test", publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00", has_content=True, content="x",
            extensions_json="{}",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/subscriptions", json={"name": "OpenAI", "filters": {"source_ids": "rss_openai"}})
        # 同一用户的第二份含 rss_openai 的订阅:订阅人数按 owner 去重,不重复计。
        client.post("/api/subscriptions", json={"name": "Dup", "filters": {"source_ids": "rss_openai"}})
        data = client.get("/api/reader/sources").json()

    # 目录是 注册源 ∪ 归档源 ∪ 已订阅源，会包含尚未产出文章的注册源，故只校验具体条目。
    by_id = {s["source_id"]: s for s in data["sources"]}
    assert by_id["rss_openai"]["count"] == 3
    assert by_id["rss_openai"]["category"] == "RSS 资讯"
    assert by_id["rss_openai"]["subscribed"] is True
    assert by_id["gh_repo"]["subscribed"] is False
    assert by_id["gh_repo"]["category"] == "代码仓库"
    # 全站订阅人数(发现页选源参考):按 owner 去重,同一用户多份订阅只计一人。
    assert by_id["rss_openai"]["subscriber_count"] == 1
    assert by_id["gh_repo"]["subscriber_count"] == 0
    assert by_id["dorami_daily_brief"]["subscriber_count"] == 1
    # 新读者账号会被默认播种精选订阅（日报 + 精选源，可取消），故并集 = 手动订阅 ∪ 默认名单。
    assert set(data["subscribed_source_ids"]) == {"rss_openai", *app_module.DEFAULT_SUBSCRIPTION_SOURCE_IDS}
    assert by_id["dorami_daily_brief"]["subscribed"] is True


def test_default_subscription_seeded_once_and_not_resurrected(monkeypatch, tmp_path):
    """新读者首次访问目录自带精选默认订阅；取消后不会被再次播种。"""
    import api.app as app_module

    sink = _make_sink(tmp_path, "defaults.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)
        # 首次访问即自带全部默认精选订阅（v3.22 扩为 5 源）
        first = client.get("/api/reader/sources").json()
        assert set(app_module.DEFAULT_SUBSCRIPTION_SOURCE_IDS) <= set(first["subscribed_source_ids"])

        # 取消订阅后应彻底移除
        client.delete("/api/reader/sources/dorami_daily_brief/subscribe")
        after_unsub = client.get("/api/reader/sources").json()
        assert "dorami_daily_brief" not in after_unsub["subscribed_source_ids"]
        # 其余默认源不受影响
        assert "web_qbitai" in after_unsub["subscribed_source_ids"]

        # 再次访问目录不会被重新播种（标记守卫生效）
        again = client.get("/api/reader/sources").json()
        assert "dorami_daily_brief" not in again["subscribed_source_ids"]


def test_default_subscriptions_seeded_at_login(monkeypatch, tmp_path):
    """播种挂在登录点：仅登录、不触碰任何 reader 端点，默认订阅即已就位。

    回归自新账号首屏竞态：此前播种只在 GET /api/reader/sources，与其并行的
    文章列表（subscribed_scope=only）/未读计数请求可能抢跑查到零订阅，
    表现为空列表 + 积压未读被误判成「载入 N 篇新文章」提示。
    """
    import api.app as app_module

    sink = _make_sink(tmp_path, "login_seed.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)

    with Session(sink.engine) as session:
        subscribed = set(app_module.resolve_subscribed_source_ids(session, "user"))
    assert set(app_module.DEFAULT_SUBSCRIPTION_SOURCE_IDS) <= subscribed


def test_login_seeding_skipped_on_collector_runtime(monkeypatch, tmp_path):
    """collector 形态无读者面，登录点不播种（原 reader 端点调用路径也不可达）。"""
    import api.app as app_module

    sink = _make_sink(tmp_path, "login_seed_collector.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "collector")

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")

    with Session(sink.engine) as session:
        assert app_module.resolve_subscribed_source_ids(session, "admin") == []


def test_articles_subscribed_scope_only_and_prioritize(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ArticleRecord

    sink = _make_sink(tmp_path, "scope.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with Session(sink.engine) as session:
        # Subscribed source: older publish_date but newer fetched_date.
        session.add(ArticleRecord(
            id="sub1", title="sub1", content_type="rss_article", source_id="rss_openai",
            source_url="https://e.test", publish_date="2026-05-10T00:00:00",
            fetched_date="2026-05-30T00:00:00", has_content=True, content="x",
            extensions_json="{}",
        ))
        # Unsubscribed source: newer publish_date but older fetched_date.
        session.add(ArticleRecord(
            id="oth1", title="oth1", content_type="rss_article", source_id="rss_other",
            source_url="https://e.test", publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-20T00:00:00", has_content=True, content="x",
            extensions_json="{}",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/subscriptions", json={"name": "OpenAI", "filters": {"source_ids": "rss_openai"}})

        default_order = [a["id"] for a in client.get("/api/articles").json()]
        assert default_order == ["oth1", "sub1"]  # publish_date newest first, not fetched_date

        only = [a["id"] for a in client.get("/api/articles?subscribed_scope=only").json()]
        assert only == ["sub1"]

        prioritized = [a["id"] for a in client.get("/api/articles?subscribed_scope=prioritize").json()]
        assert prioritized == ["sub1", "oth1"]  # subscribed first despite older


def test_articles_can_return_total_without_breaking_list_response(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ArticleRecord

    sink = _make_sink(tmp_path, "articles_total.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with Session(sink.engine) as session:
        for index in range(3):
            session.add(ArticleRecord(
                id=f"a{index}", title=f"a{index}", content_type="rss_article", source_id="rss_openai",
                source_url="https://e.test", publish_date=f"2026-05-2{index}T00:00:00",
                fetched_date=f"2026-05-2{index}T00:00:00", has_content=True, content="x",
                extensions_json="{}",
            ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        legacy = client.get("/api/articles?limit=2").json()
        assert isinstance(legacy, list)
        assert len(legacy) == 2

        paged = client.get("/api/articles?limit=2&include_total=true").json()
        assert paged["total"] == 3
        assert paged["limit"] == 2
        assert paged["skip"] == 0
        assert paged["next_skip"] == 2
        assert len(paged["items"]) == 2


def test_articles_lightweight_list_and_detail_endpoint(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ArticleRecord

    sink = _make_sink(tmp_path, "articles_lightweight.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    body = "正文" * 300
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="article_full", title="full", content_type="rss_article", source_id="rss_openai",
            source_url="https://e.test/full", publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-20T00:00:00", has_content=True, content=body,
            extensions_json='{"tag": "full"}',
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)

        legacy = client.get("/api/articles").json()[0]
        assert legacy["content"] == body
        assert legacy["extensions_json"] == '{"tag": "full"}'

        slim = client.get("/api/articles?include_content=false&include_total=true").json()["items"][0]
        assert slim["content_preview"] == body[:280]
        assert "content" not in slim
        assert "extensions_json" not in slim

        detail = client.get("/api/articles/article_full").json()
        assert detail["content"] == body
        assert detail["extensions_json"] == '{"tag": "full"}'


def test_articles_subscribed_only_empty_when_no_subscriptions(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ArticleRecord

    sink = _make_sink(tmp_path, "scope_empty.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="x1", title="x1", content_type="rss_article", source_id="rss_openai",
            source_url="https://e.test", publish_date="2026-05-10T00:00:00",
            fetched_date="2026-05-10T00:00:00", has_content=True, content="x",
            extensions_json="{}",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        assert client.get("/api/articles?subscribed_scope=only").json() == []


def test_public_subscription_vector_search_scopes_to_sources(monkeypatch, tmp_path):
    """令牌检索端点(v3.31 起 FTS5 芯):作用域限定到订阅来源,域外命中不外泄。"""
    import api.app as app_module

    sink = _make_sink(tmp_path, "pubvec.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article(sink.engine, "o1", "rss_openai", "Agent news from OpenAI")
    _seed_article(sink.engine, "e1", "rss_elsewhere", "Agent news elsewhere")

    with TestClient(app_module.app) as client:
        _login(client)
        sub = client.post(
            "/api/subscriptions",
            json={"name": "OpenAI", "filters": {"source_ids": "rss_openai,rss_anthropic"}},
        ).json()

    with TestClient(app_module.app) as public:
        # Missing/invalid token rejected.
        assert public.post(f"/api/public/subscriptions/{sub['id']}/vector/search", json={"query": "x"}).status_code == 401
        # Valid token scopes search to the subscription's sources.
        resp = public.post(
            f"/api/public/subscriptions/{sub['id']}/vector/search?token={sub['token']}",
            json={"query": "Agent"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert sorted(data["scoped_source_ids"]) == ["rss_anthropic", "rss_openai"]
        assert data["count"] == 1
        assert data["results"][0]["id"] == "o1"
        assert data["results"][0]["source_id"] == "rss_openai"


def test_public_subscription_vector_search_disabled_in_collector(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "pubvec_col.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "collector")

    with TestClient(app_module.app) as client:
        resp = client.post("/api/public/subscriptions/1/vector/search?token=anything", json={"query": "x"})
        assert resp.status_code == 403


def test_resolve_subscription_sources_by_token(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "mcptok.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        _login(client)
        sub = client.post(
            "/api/subscriptions",
            json={"name": "S", "filters": {"source_ids": "rss_a,rss_b"}},
        ).json()

    assert app_module.resolve_subscription_sources_by_token(sub["token"]) == ["rss_a", "rss_b"]
    assert app_module.resolve_subscription_sources_by_token("dsub_bogus") is None
    assert app_module.resolve_subscription_sources_by_token("") is None


def test_mcp_browse_impl_scopes_by_source_ids(tmp_path):
    import mcp_server
    from models.db import ArticleRecord
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'mcpbrowse.db'}")
    with Session(sink.engine) as session:
        for sid in ["rss_a", "rss_b", "rss_c"]:
            session.add(ArticleRecord(
                id=f"{sid}_1", title=sid, content_type="rss_article", source_id=sid,
                source_url="https://e.test", publish_date="2026-05-20T00:00:00",
                fetched_date="2026-05-20T00:00:00", has_content=True, content="x",
                extensions_json="{}",
            ))
        session.commit()

    rows = mcp_server._browse_articles_impl(sink, source_ids=["rss_a", "rss_b"])
    assert {r["source_id"] for r in rows} == {"rss_a", "rss_b"}
    # Empty subscription scope must match nothing, not fall through to all.
    assert mcp_server._browse_articles_impl(sink, source_ids=[]) == []


def test_resolve_subscribed_source_ids_unions_active_subscriptions(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ReaderSubscriptionRecord

    sink = _make_sink(tmp_path, "subsources.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    now = "2026-05-26T00:00:00"
    with Session(sink.engine) as session:
        session.add(ReaderSubscriptionRecord(
            owner_username="alice", name="A", filters_json='{"source_ids": "rss_openai,rss_anthropic"}',
            token_hash="h1", is_active=True, created_at=now, updated_at=now,
        ))
        session.add(ReaderSubscriptionRecord(
            owner_username="alice", name="B", filters_json='{"source_id": "rss_hf"}',
            token_hash="h2", is_active=True, created_at=now, updated_at=now,
        ))
        session.add(ReaderSubscriptionRecord(
            owner_username="alice", name="C-disabled", filters_json='{"source_ids": "rss_secret"}',
            token_hash="h3", is_active=False, created_at=now, updated_at=now,
        ))
        session.add(ReaderSubscriptionRecord(
            owner_username="bob", name="D", filters_json='{"source_ids": "rss_bob"}',
            token_hash="h4", is_active=True, created_at=now, updated_at=now,
        ))
        session.commit()

        ids = app_module.resolve_subscribed_source_ids(session, "alice")

    assert ids == ["rss_anthropic", "rss_hf", "rss_openai"]


def test_one_click_subscribe_creates_single_source_subscription(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "oneclick.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _mark_defaults_seeded(sink.engine)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article(sink.engine, "o1", "rss_openai", "OpenAI Update")

    with TestClient(app_module.app) as client:
        _login(client)
        res = client.post("/api/reader/sources/rss_openai/subscribe")
        assert res.status_code == 200
        body = res.json()
        assert body["subscribed"] is True
        assert body["subscribed_source_ids"] == ["rss_openai"]

        # 幂等：再次订阅不应新建第二个订阅
        client.post("/api/reader/sources/rss_openai/subscribe")
        subs = client.get("/api/subscriptions").json()
        assert len(subs) == 1
        assert subs[0]["filters"].get("source_ids") == "rss_openai"

        catalog = client.get("/api/reader/sources").json()
        assert {s["source_id"]: s["subscribed"] for s in catalog["sources"]}["rss_openai"] is True


def test_one_click_unsubscribe_removes_source_and_prunes_empty(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ReaderSubscriptionRecord

    sink = _make_sink(tmp_path, "oneclick_off.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _mark_defaults_seeded(sink.engine)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article(sink.engine, "o1", "rss_openai", "OpenAI Update")
    _seed_article(sink.engine, "a1", "rss_anthropic", "Anthropic Update")

    now = "2026-05-26T00:00:00"
    with Session(sink.engine) as session:
        # 单源订阅（取消后应被删除）
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="OpenAI", filters_json='{"source_ids": "rss_openai"}',
            token_hash="h1", is_active=True, created_at=now, updated_at=now,
        ))
        # 多源订阅（取消其中一个源后应保留，仅剥离该源）
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="Mix", filters_json='{"source_ids": "rss_openai,rss_anthropic"}',
            token_hash="h2", is_active=True, created_at=now, updated_at=now,
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        res = client.delete("/api/reader/sources/rss_openai/subscribe")
        assert res.status_code == 200
        assert res.json()["subscribed_source_ids"] == ["rss_anthropic"]

        subs = client.get("/api/subscriptions").json()
        names = {s["name"]: s for s in subs}
        assert "OpenAI" not in names  # 单源订阅被删除
        assert names["Mix"]["filters"].get("source_ids") == "rss_anthropic"  # 多源订阅仅剥离


def test_reader_sources_catalog_enriches_name_from_registry(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "enrich.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    # 选一个真实注册的抓取器 source_id，验证目录用注册表的中文/英文名而非下划线代号。
    meta = app_module.fetcher_registry.get_all_metadata()
    assert meta, "fetcher registry should expose at least one source"
    sample = meta[0]
    _seed_article(sink.engine, "e1", sample["id"], "sample")

    with TestClient(app_module.app) as client:
        _login(client)
        data = client.get("/api/reader/sources").json()

    entry = {s["source_id"]: s for s in data["sources"]}[sample["id"]]
    assert entry["name"] == sample["name"]
    assert entry["description"] == sample["desc"]


def test_reader_sources_includes_zero_article_registered_source(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "zero.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    # 一个真实注册、但库中没有任何归档文章的源，应当出现在目录里并可被订阅。
    # 但 is_template 模板源（generic_*）是 source-config/builder 的执行基座、非可订阅来源，
    # 不应出现在读者目录（X 社交波「隐藏通用 XXX 源」契约）。
    meta = app_module.fetcher_registry.get_all_metadata()
    assert meta, "fetcher registry should expose at least one source"
    fresh = next(m["id"] for m in meta if not m.get("is_template"))
    template_ids = [m["id"] for m in meta if m.get("is_template")]

    with TestClient(app_module.app) as client:
        _login(client)
        catalog = client.get("/api/reader/sources").json()
        catalog_ids = {s["source_id"] for s in catalog["sources"]}
        # 模板源被读者目录排除
        for tid in template_ids:
            assert tid not in catalog_ids, f"模板源 {tid} 不应出现在读者可订阅目录"
        entry = {s["source_id"]: s for s in catalog["sources"]}.get(fresh)
        assert entry is not None
        assert entry["count"] == 0  # 历史产出为 0
        assert entry["subscribed"] is False

        res = client.post(f"/api/reader/sources/{fresh}/subscribe")
        assert res.status_code == 200
        assert fresh in res.json()["subscribed_source_ids"]

        catalog2 = client.get("/api/reader/sources").json()
        assert {s["source_id"]: s for s in catalog2["sources"]}[fresh]["subscribed"] is True


def test_reader_sources_excludes_decommissioned_node_with_lingering_archive(monkeypatch, tmp_path):
    import api.app as app_module
    from models.db import ArticleRecord
    from fetchers.registry import DECOMMISSIONED_FETCHER_IDS

    sink = _make_sink(tmp_path, "decommissioned.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    dead = next(iter(DECOMMISSIONED_FETCHER_IDS))  # 已删类、但历史归档仍在的下线节点

    with Session(sink.engine) as session:
        # 下线节点遗留的归档文章。
        session.add(ArticleRecord(
            id="d1", title="dead", content_type="rss_article", source_id=dead,
            source_url="https://e.test", publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00", has_content=True, content="x",
            extensions_json="{}",
        ))
        # 合法的未注册导入源（如 social_post）不应被一并误伤。
        session.add(ArticleRecord(
            id="s1", title="post", content_type="social_post", source_id="import_x_feed",
            source_url="https://e.test", publish_date="2026-05-20T00:00:00",
            fetched_date="2026-05-21T00:00:00", has_content=True, content="x",
            extensions_json="{}",
        ))
        session.commit()

    with TestClient(app_module.app) as client:
        _login(client)
        by_id = {s["source_id"]: s for s in client.get("/api/reader/sources").json()["sources"]}
        # 下线节点不回流目录；导入源仍可订阅。
        assert dead not in by_id
        assert "import_x_feed" in by_id

        # 但若用户已订阅该下线节点，目录仍要展示它（携带归档计数），以便退订。
        assert client.post(f"/api/reader/sources/{dead}/subscribe").status_code == 200
        entry = {s["source_id"]: s for s in client.get("/api/reader/sources").json()["sources"]}.get(dead)
        assert entry is not None
        assert entry["subscribed"] is True
        assert entry["count"] == 1


def _seed_article_dated(
        engine,
        article_id,
        source_id,
        title,
        publish_date,
        *,
        content_type="rss_article",
        fetched_date="2026-05-26T00:00:00",
        content=None,
        source_url=None,
):
    from models.db import ArticleRecord
    with Session(engine) as session:
        session.add(ArticleRecord(
            id=article_id, title=title, content_type=content_type, source_id=source_id,
            source_url=source_url or f"https://example.test/{article_id}",
            publish_date=publish_date, fetched_date=fetched_date, has_content=True,
            content=content if content is not None else f"{title} body",
            extensions_json="{}",
        ))
        session.commit()


def test_personal_feed_pulls_all_subscribed_sources_with_filters(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feed.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article_dated(sink.engine, "o1", "rss_openai", "OpenAI old", "2026-05-01T00:00:00")
    _seed_article_dated(sink.engine, "o2", "rss_openai", "OpenAI new", "2026-05-25T00:00:00")
    _seed_article_dated(sink.engine, "a1", "rss_anthropic", "Anthropic new", "2026-05-24T00:00:00")
    _seed_article_dated(sink.engine, "x1", "rss_other", "Not subscribed", "2026-05-25T00:00:00")

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/rss_openai/subscribe")
        client.post("/api/reader/sources/rss_anthropic/subscribe")
        token = client.post("/api/reader/feed-token/rotate").json()["token"]
        assert token.startswith("dfeed_")

        # 覆盖全部已订阅来源，未订阅来源不出现
        data = client.get("/api/public/feed/articles", headers={"Authorization": f"Bearer {token}"}).json()
        ids = {item["id"] for item in data["items"]}
        assert ids == {"o1", "o2", "a1"}

        # 按发布时间筛选（日报场景）
        recent = client.get(
            "/api/public/feed/articles?publish_date_start=2026-05-20",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert {item["id"] for item in recent["items"]} == {"o2", "a1"}

        # source_ids 取与已订阅集合的交集，未订阅 ID 被忽略
        scoped = client.get(
            "/api/public/feed/articles?source_ids=rss_openai,rss_other",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert {item["id"] for item in scoped["items"]} == {"o1", "o2"}


def test_personal_feed_requires_valid_token(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feedauth.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        assert client.get("/api/public/feed/articles").status_code == 401
        assert client.get("/api/public/feed/articles?token=dfeed_wrong").status_code == 401


def test_personal_feed_atom_is_valid_and_escapes_special_characters(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feed_atom.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    title = "R&D <Launch> > Alpha"
    content = "Markdown & details <tag> > ending"
    source_url = "https://example.test/special?a=1&b=<two>"
    _seed_article_dated(
        sink.engine,
        "special",
        "atom_special_source",
        title,
        "",
        fetched_date="2026-05-27T04:05:06+00:00",
        content=content,
        source_url=source_url,
    )
    _seed_article_dated(
        sink.engine,
        "older",
        "atom_special_source",
        "Older entry",
        "2026-05-20T00:00:00Z",
    )

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/atom_special_source/subscribe")
        token = client.post("/api/reader/feed-token/rotate").json()["token"]
        response = client.get(
            "/api/public/feed/articles.xml",
            headers={"Authorization": f"Bearer {token}"},
        )
        summary_response = client.get(
            "/api/public/feed/articles.xml?include_content=false",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/atom+xml")
    root = ET.fromstring(response.content)
    atom = {"atom": "http://www.w3.org/2005/Atom"}
    assert root.tag == "{http://www.w3.org/2005/Atom}feed"
    assert root.findtext("atom:id", namespaces=atom).startswith("tag:dorami.local,2026:feed:")
    assert root.findtext("atom:title", namespaces=atom) == "哆啦美订阅聚合 · user"
    assert root.findtext("atom:updated", namespaces=atom) == "2026-05-27T04:05:06Z"

    entries = root.findall("atom:entry", atom)
    assert len(entries) == 2
    special = next(entry for entry in entries if entry.findtext("atom:title", namespaces=atom) == title)
    assert special.findtext("atom:id", namespaces=atom) == source_url
    assert special.find("atom:link", atom).attrib["href"] == source_url
    assert special.findtext("atom:updated", namespaces=atom) == "2026-05-27T04:05:06Z"
    assert special.findtext("atom:published", namespaces=atom) == "2026-05-27T04:05:06Z"
    assert special.findtext("atom:author/atom:name", namespaces=atom) == "atom_special_source"
    content_node = special.find("atom:content", atom)
    assert content_node.attrib["type"] == "text"
    assert content_node.text == content
    assert {
        category.attrib["term"] for category in special.findall("atom:category", atom)
    } == {"rss_article", "atom_special_source"}
    assert b"R&amp;D &lt;Launch&gt; &gt; Alpha" in response.content
    assert b"Markdown &amp; details &lt;tag&gt; &gt; ending" in response.content
    summary_root = ET.fromstring(summary_response.content)
    summary_entry = next(
        entry
        for entry in summary_root.findall("atom:entry", atom)
        if entry.findtext("atom:title", namespaces=atom) == title
    )
    assert summary_entry.find("atom:content", atom) is None
    assert summary_entry.findtext("atom:summary", namespaces=atom) == content


def test_personal_feed_atom_filters_narrow_entries(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feed_atom_filters.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article_dated(
        sink.engine, "target", "atom_source_a", "Needle target",
        "2026-05-20T00:00:00", content_type="web_article",
    )
    _seed_article_dated(
        sink.engine, "wrong_type", "atom_source_a", "Needle RSS",
        "2026-05-20T00:00:00", content_type="rss_article",
    )
    _seed_article_dated(
        sink.engine, "other_source", "atom_source_b", "Other source",
        "2026-05-20T00:00:00", content_type="web_article",
    )
    _seed_article_dated(
        sink.engine, "search_hit", "atom_source_a", "Haystack marker",
        "2026-05-20T00:00:00", content_type="web_article",
    )
    _seed_article_dated(
        sink.engine, "old", "atom_source_a", "Needle old",
        "2026-05-10T00:00:00", content_type="web_article",
    )
    _seed_article_dated(
        sink.engine, "future", "atom_source_a", "Needle future",
        "2026-05-30T00:00:00", content_type="web_article",
    )

    with TestClient(app_module.app) as client:
        _login(client)
        client.post("/api/reader/sources/atom_source_a/subscribe")
        client.post("/api/reader/sources/atom_source_b/subscribe")
        token = client.post("/api/reader/feed-token/rotate").json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        def entry_urls(query):
            response = client.get(f"/api/public/feed/articles.xml?{query}", headers=headers)
            assert response.status_code == 200
            root = ET.fromstring(response.content)
            namespace = {"atom": "http://www.w3.org/2005/Atom"}
            return {
                entry.find("atom:link", namespace).attrib["href"]
                for entry in root.findall("atom:entry", namespace)
            }

        assert entry_urls("content_types=web_article") == {
            "https://example.test/target",
            "https://example.test/other_source",
            "https://example.test/search_hit",
            "https://example.test/old",
            "https://example.test/future",
        }
        assert entry_urls("source_ids=atom_source_b") == {
            "https://example.test/other_source"
        }
        assert entry_urls("search=Haystack") == {
            "https://example.test/search_hit"
        }
        ranged = entry_urls(
            "publish_date_start=2026-05-19&publish_date_end=2026-05-21"
        )
        assert ranged == {
            "https://example.test/target",
            "https://example.test/wrong_type",
            "https://example.test/other_source",
            "https://example.test/search_hit",
        }


def test_personal_feed_atom_invalid_token_matches_markdown(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feed_atom_auth.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")

    with TestClient(app_module.app) as client:
        for suffix in ("", "?token=dfeed_wrong"):
            markdown = client.get(f"/api/public/feed/articles.md{suffix}")
            atom = client.get(f"/api/public/feed/articles.xml{suffix}")
            assert atom.status_code == markdown.status_code == 401
            assert atom.json() == markdown.json() == {
                "detail": "个人聚合接口令牌无效"
            }


def test_personal_feed_disabled_in_collector_role(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feedcollector.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "collector")

    with TestClient(app_module.app) as client:
        assert client.get("/api/public/feed/articles?token=anything").status_code == 403


def test_feed_token_per_user_isolated(monkeypatch, tmp_path):
    import api.app as app_module

    sink = _make_sink(tmp_path, "feedowners.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _seed_users(sink.engine, (("alice", "alice", "user"), ("bob", "bob", "user")))
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article_dated(sink.engine, "a1", "rss_alice", "Alice src", "2026-05-25T00:00:00")
    _seed_article_dated(sink.engine, "b1", "rss_bob", "Bob src", "2026-05-25T00:00:00")

    with TestClient(app_module.app) as alice:
        _login(alice, "alice", "alice")
        alice.post("/api/reader/sources/rss_alice/subscribe")
        alice_token = alice.post("/api/reader/feed-token/rotate").json()["token"]

    with TestClient(app_module.app) as bob:
        _login(bob, "bob", "bob")
        bob.post("/api/reader/sources/rss_bob/subscribe")

    # Alice 的令牌只能拉到 Alice 订阅的来源
    with TestClient(app_module.app) as public:
        data = public.get("/api/public/feed/articles", headers={"Authorization": f"Bearer {alice_token}"}).json()
        assert {item["id"] for item in data["items"]} == {"a1"}


def test_personal_feed_admin_token_covers_whole_archive(monkeypatch, tmp_path):
    """管理员不设订阅，其 dfeed_ 令牌直通全库；显式 source_ids 原样生效。"""
    import api.app as app_module
    from api.feed_service import resolve_subscription_sources_by_token

    sink = _make_sink(tmp_path, "feedadmin.db")
    monkeypatch.setattr(app_module, "db_sink", sink)
    _set_auth_accounts(monkeypatch, app_module)
    _set_runtime_role(monkeypatch, app_module, "reader")
    _seed_article_dated(sink.engine, "o1", "rss_openai", "OpenAI", "2026-05-25T00:00:00")
    _seed_article_dated(sink.engine, "x1", "rss_other", "Other", "2026-05-24T00:00:00")

    with TestClient(app_module.app) as client:
        _login(client, "admin", "admin")
        token = client.post("/api/reader/feed-token/rotate").json()["token"]

        # 零订阅仍拉到全库（读者账号此场景返回空）
        data = client.get("/api/public/feed/articles", headers={"Authorization": f"Bearer {token}"}).json()
        assert {item["id"] for item in data["items"]} == {"o1", "x1"}

        # 显式 source_ids 不做订阅交集，原样生效
        scoped = client.get(
            "/api/public/feed/articles?source_ids=rss_other",
            headers={"Authorization": f"Bearer {token}"},
        ).json()
        assert {item["id"] for item in scoped["items"]} == {"x1"}

        # MCP 作用域解析：管理员令牌 → []（调用方契约中的「未限定来源」）
        assert resolve_subscription_sources_by_token(token) == []
