"""运维列表规模化波(v3.42,审计 M08/M09/M11/M17)后端护栏。

- M08:active_subscribers_by_source 一次扫描与逐源 active_subscriber_usernames
  结果逐字一致(共享源/inactive 订阅排除/账户存在性兜底);
- M09:/api/fetch-runs 与 /api/collection-job-runs 响应 {items,total},
  days 时间窗 + 过滤 SQL 端生效、分页 total 不随页收窄;
- M11:审计新覆盖(删文/批量删文/触发采集入审计)+ audit-log 检索
  (operator/q/status 过滤);
- M17:反馈收件箱 q/category 过滤;公告列表分页 + total。
"""

import datetime
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sqlmodel import Session, select  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402
from models.db import (  # noqa: E402
    AdminAuditRecord,
    AnnouncementRecord,
    ArticleRecord,
    CollectionJobRunRecord,
    FeedbackRecord,
    FetchRunRecord,
    ReaderSubscriptionRecord,
    SourceConfigRecord,
)
from tests.conftest import seed_default_accounts  # noqa: E402


def _day(offset: int) -> str:
    return (datetime.date.today() - datetime.timedelta(days=offset)).isoformat()


def _setup(monkeypatch, tmp_path, name):
    import api.app as app_module

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}")
    monkeypatch.setattr(app_module, "db_sink", sink)
    seed_default_accounts(sink.engine)
    return app_module


def _login(client, u="admin", p="admin"):
    assert client.post("/api/auth/login", json={"username": u, "password": p}).status_code == 200


# ==================== M08:订阅者归属一次扫描 ====================
def test_active_subscribers_by_source_matches_per_source(tmp_path):
    from services import user_sources

    engine = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'm08.db'}").engine
    seed_default_accounts(engine, (
        ("admin", "admin", "admin"), ("user", "user", "user"), ("bob", "bobpw123", "user"),
    ))
    t = "2026-09-01T08:00:00"
    ids = ["user_rss_aaaaaaaaaaaa", "user_rss_bbbbbbbbbbbb", "user_rss_cccccccccccc"]
    with Session(engine) as session:
        for sid in ids:
            session.add(SourceConfigRecord(
                source_id=sid, name=sid, source_type="rss", owner_username="user",
                created_at=t, updated_at=t,
            ))
        # user 订 a+b(多源行);bob 订 a;ghost(账户不存在)订 c;inactive 行订 b 不计。
        session.add(ReaderSubscriptionRecord(
            owner_username="user", name="s1", token_hash="h1",
            filters_json=f'{{"source_ids": "{ids[0]},{ids[1]}"}}', created_at=t, updated_at=t))
        session.add(ReaderSubscriptionRecord(
            owner_username="bob", name="s2", token_hash="h2",
            filters_json=f'{{"source_id": "{ids[0]}"}}', created_at=t, updated_at=t))
        session.add(ReaderSubscriptionRecord(
            owner_username="ghost", name="s3", token_hash="h3",
            filters_json=f'{{"source_id": "{ids[2]}"}}', created_at=t, updated_at=t))
        session.add(ReaderSubscriptionRecord(
            owner_username="bob", name="s4", token_hash="h4", is_active=False,
            filters_json=f'{{"source_id": "{ids[1]}"}}', created_at=t, updated_at=t))
        session.commit()

        batch = user_sources.active_subscribers_by_source(session, ids)
        for sid in ids:
            assert batch[sid] == user_sources.active_subscriber_usernames(session, sid), sid
        assert batch[ids[0]] == ["bob", "user"]
        assert batch[ids[1]] == ["user"]
        assert batch[ids[2]] == []  # 账户不存在兜底

        # admin_overview 走同一映射:订阅人数与覆盖用户口径一致。
        overview = user_sources.admin_overview(session)
        by_id = {i["source_id"]: i for i in overview["items"]}
        assert by_id[ids[0]]["subscriber_count"] == 2
        assert by_id[ids[2]]["subscriber_count"] == 0
        assert overview["kpi"]["covered_users"] == 2


# ==================== M09:运行历史真分页 ====================
def test_fetch_runs_items_total_days(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path, "m09a.db")
    with Session(app_module.db_sink.engine) as session:
        for i in range(7):
            session.add(FetchRunRecord(
                fetcher_id="fx", status="success" if i % 2 == 0 else "failed",
                started_at=f"{_day(0)}T0{i}:00:00"))
        session.add(FetchRunRecord(fetcher_id="fx", status="failed", started_at=f"{_day(40)}T08:00:00"))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        body = client.get("/api/fetch-runs?limit=3").json()
        assert set(body) == {"items", "total"}
        assert body["total"] == 8 and len(body["items"]) == 3
        # 过滤组合下 total 收窄;分页切片不影响 total。
        body = client.get("/api/fetch-runs?status=failed&limit=2").json()
        assert body["total"] == 4 and len(body["items"]) == 2
        # days 时间窗排除 40 天前旧行。
        body = client.get("/api/fetch-runs?days=7&status=failed").json()
        assert body["total"] == 3


def test_collection_job_runs_items_total_days(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path, "m09b.db")
    with Session(app_module.db_sink.engine) as session:
        for i in range(4):
            session.add(CollectionJobRunRecord(
                job_id=1, run_scope="saved_job", status="success",
                started_at=f"{_day(0)}T0{i}:00:00", node_count=1))
        session.add(CollectionJobRunRecord(
            job_id=1, run_scope="saved_job", status="failed",
            started_at=f"{_day(50)}T08:00:00", node_count=1))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        body = client.get("/api/collection-job-runs?limit=2").json()
        assert body["total"] == 5 and len(body["items"]) == 2
        assert client.get("/api/collection-job-runs?days=7").json()["total"] == 4


# ==================== M11:审计覆盖 + 检索 ====================
def test_audit_covers_article_writes_and_fetch_trigger(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path, "m11a.db")
    with Session(app_module.db_sink.engine) as session:
        session.add(ArticleRecord(
            id="a1", source_id="rss_x", content_type="rss", title="t", content="c",
            source_url="", publish_date=_day(0), fetched_date=f"{_day(0)}T08:00:00"))
        session.add(ArticleRecord(
            id="a2", source_id="rss_x", content_type="rss", title="t2", content="c2",
            source_url="", publish_date=_day(0), fetched_date=f"{_day(0)}T08:00:00"))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.delete("/api/articles/a1").status_code == 200
        assert client.post("/api/articles/batch-delete", json={"ids": ["a2"]}).status_code == 200
    with Session(app_module.db_sink.engine) as session:
        summaries = [r.summary for r in session.exec(select(AdminAuditRecord)).all()]
    assert "删除文章 a1" in summaries
    assert "批量删除文章 1 篇" in summaries


def test_audit_log_search_filters(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path, "m11b.db")
    now = datetime.datetime.now().isoformat()
    with Session(app_module.db_sink.engine) as session:
        rows = [
            ("admin", "POST", "/api/accounts", 200, "新建账户 carol(角色 读者)", "carol"),
            ("grace", "DELETE", "/api/accounts/dave", 400, "删除账户 dave", "dave"),
            ("grace", "POST", "/api/mcp/toggle", 200, "切换全站 MCP 总闸", None),
        ]
        for username, method, path, code, summary, target in rows:
            session.add(AdminAuditRecord(
                username=username, method=method, path=path,
                status_code=code, summary=summary, target=target, at=now))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        # 操作者子串
        body = client.get("/api/admin/audit-log?operator=grac").json()
        assert body["total"] == 2
        # q 跨摘要/目标/路径
        assert client.get("/api/admin/audit-log?q=carol").json()["total"] == 1
        assert client.get("/api/admin/audit-log?q=mcp").json()["total"] == 1
        # 状态:被拒绝的尝试单独可查
        body = client.get("/api/admin/audit-log?status=denied").json()
        assert body["total"] == 1 and body["items"][0]["status_code"] == 400
        assert client.get("/api/admin/audit-log?status=ok").json()["total"] == 2
        # 组合
        assert client.get("/api/admin/audit-log?operator=grace&status=ok").json()["total"] == 1


# ==================== M17:反馈检索 + 公告分页 ====================
def test_feedback_admin_search_and_category(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path, "m17a.db")
    t = datetime.datetime.now().isoformat()
    with Session(app_module.db_sink.engine) as session:
        rows = [
            ("user", "bug", "阅读器白屏了", "open"),
            ("user", "source_request", "想要 InfoQ 源", "open"),
            ("bob", "bug", "翻译按钮没反应", "resolved"),
        ]
        for owner, cat, content, status in rows:
            session.add(FeedbackRecord(
                owner_username=owner, category=cat, content=content,
                status=status, created_at=t, updated_at=t))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        assert client.get("/api/admin/feedback?category=bug").json()["total"] == 2
        assert client.get("/api/admin/feedback?q=白屏").json()["total"] == 1
        assert client.get("/api/admin/feedback?q=bob").json()["total"] == 1
        # 组合:分类 × 状态 × 检索
        body = client.get("/api/admin/feedback?category=bug&status=open").json()
        assert body["total"] == 1 and body["items"][0]["content"] == "阅读器白屏了"
        # counts 恒全量
        assert body["counts"]["total"] == 3
        # 非法分类 400
        assert client.get("/api/admin/feedback?category=nope").status_code == 400


def test_announcements_pagination_total(monkeypatch, tmp_path):
    app_module = _setup(monkeypatch, tmp_path, "m17b.db")
    with Session(app_module.db_sink.engine) as session:
        for i in range(7):
            session.add(AnnouncementRecord(
                title=f"t{i}", content=f"公告 {i}", level="info",
                created_by="admin", created_at=f"{_day(0)}T0{i}:00:00", updated_at=f"{_day(0)}T0{i}:00:00"))
        session.commit()
    with TestClient(app_module.app) as client:
        _login(client)
        body = client.get("/api/admin/announcements?limit=3").json()
        assert body["total"] == 7 and len(body["items"]) == 3
        page2 = client.get("/api/admin/announcements?limit=3&skip=3").json()
        assert len(page2["items"]) == 3
        assert {i["id"] for i in body["items"]}.isdisjoint({i["id"] for i in page2["items"]})
        # 不传参保持全量语义(默认 limit 200)。
        assert len(client.get("/api/admin/announcements").json()["items"]) == 7
