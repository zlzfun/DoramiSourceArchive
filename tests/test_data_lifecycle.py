"""数据生命周期与可靠性波（v3.43 审计 M13/M14/M16/M21/M22）护栏测试。

- **M14 留存扩面**：collection_job_runs 纯时间窗；jobs/feedbacks/article_shares
  条件性清理——非终态/未了结/存活行无论多老都保留。
- **M21 计量唯一约束**：聚合键唯一索引 + 写侧 ON CONFLICT 原子累加；
  迁移 a7e2f95c1d40 对存量重复行求和合并（回放测试）。
- **M16 响应裁剪**：summarize 的 by_day_user 服务端 Top-N + 「其它」，总量守恒。
- **M22**：单用户最近登录兜底改单人 MAX 标量查询。
- **M13**：source-health 的运行史回退仅服务无 state 快照的节点（有 state 走快照、
  无 state 的回退聚合不回归）。
"""

import datetime
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from models.db import (  # noqa: E402
    AiUsageRecord,
    ArticleShareRecord,
    CollectionJobRunRecord,
    FeedbackRecord,
    FetchRunRecord,
    JobRecord,
    ReaderReadRecord,
    SourceStateRecord,
)
from services import ai_usage, reader_activity, retention  # noqa: E402
from services.accounts import create_user, last_login_for_user, touch_login  # noqa: E402
from storage.impl.db_storage import DatabaseStorage  # noqa: E402


def _engine(tmp_path, name="lifecycle.db"):
    return DatabaseStorage(db_url=f"sqlite:///{tmp_path / name}").engine


def _iso_days_ago(days: int) -> str:
    return (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()


# ══ M14 留存扩面 ══════════════════════════════════════════════════


def test_retention_collection_job_runs_window(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(CollectionJobRunRecord(name="old", started_at=_iso_days_ago(200)))
        session.add(CollectionJobRunRecord(name="recent", started_at=_iso_days_ago(10)))
        session.commit()
    deleted = retention.run_retention_cleanup(engine)
    assert deleted["collection_job_runs"] == 1
    with Session(engine) as session:
        rows = session.exec(select(CollectionJobRunRecord)).all()
        assert [r.name for r in rows] == ["recent"]


def test_retention_jobs_only_terminal_and_old(tmp_path):
    engine = _engine(tmp_path)
    now = time.time()
    old = now - 120 * 86400
    with Session(engine) as session:
        session.add(JobRecord(id="t-old", type="x", status="succeeded", created_at=old, ended_at=old))
        session.add(JobRecord(id="t-fail-old", type="x", status="failed", created_at=old, ended_at=old))
        # 非终态无论多老都保留（在途任务是活体）。
        session.add(JobRecord(id="run-old", type="x", status="running", created_at=old))
        session.add(JobRecord(id="q-old", type="x", status="queued", created_at=old))
        # 终态但在窗口内保留。
        session.add(JobRecord(id="t-new", type="x", status="succeeded", created_at=now, ended_at=now))
        # ended_at 缺失的终态行按 created_at 裁决。
        session.add(JobRecord(id="t-noend-old", type="x", status="cancelled", created_at=old))
        session.commit()
    deleted = retention.run_retention_cleanup(engine)
    assert deleted["jobs"] == 3
    with Session(engine) as session:
        kept = {j.id for j in session.exec(select(JobRecord)).all()}
        assert kept == {"run-old", "q-old", "t-new"}


def test_retention_feedback_only_closed_and_old(tmp_path):
    engine = _engine(tmp_path)
    old, recent = _iso_days_ago(400), _iso_days_ago(5)
    with Session(engine) as session:
        session.add(FeedbackRecord(owner_username="a", category="bug", content="老已了结",
                                   status="resolved", created_at=old, updated_at=old))
        session.add(FeedbackRecord(owner_username="a", category="bug", content="老已关闭",
                                   status="dismissed", created_at=old, updated_at=old))
        # 未处理的诉求不因久拖而蒸发。
        session.add(FeedbackRecord(owner_username="a", category="bug", content="老 open",
                                   status="open", created_at=old, updated_at=old))
        session.add(FeedbackRecord(owner_username="a", category="bug", content="老处理中",
                                   status="in_progress", created_at=old, updated_at=old))
        session.add(FeedbackRecord(owner_username="a", category="bug", content="新已了结",
                                   status="resolved", created_at=recent, updated_at=recent))
        session.commit()
    deleted = retention.run_retention_cleanup(engine)
    assert deleted["feedbacks"] == 2
    with Session(engine) as session:
        kept = {f.content for f in session.exec(select(FeedbackRecord)).all()}
        assert kept == {"老 open", "老处理中", "新已了结"}


def test_retention_shares_only_dead_and_old(tmp_path):
    engine = _engine(tmp_path)
    old, recent = _iso_days_ago(200), _iso_days_ago(5)
    with Session(engine) as session:
        session.add(ArticleShareRecord(token="dshr_dead1", article_id="a1", owner_username="u",
                                       created_at=old, revoked_at=old))
        session.add(ArticleShareRecord(token="dshr_dead2", article_id="a2", owner_username="u",
                                       created_at=old, expires_at=old))
        # 存活链接（含永久档）与刚失效的都保留。
        session.add(ArticleShareRecord(token="dshr_live", article_id="a3", owner_username="u",
                                       created_at=old, expires_at=None))
        session.add(ArticleShareRecord(token="dshr_fresh_dead", article_id="a4", owner_username="u",
                                       created_at=recent, revoked_at=recent))
        session.commit()
    deleted = retention.run_retention_cleanup(engine)
    assert deleted["article_shares"] == 2
    with Session(engine) as session:
        kept = {s.token for s in session.exec(select(ArticleShareRecord)).all()}
        assert kept == {"dshr_live", "dshr_fresh_dead"}


def test_retention_shares_dual_timestamp_dead(tmp_path):
    """v3.43.1 codex 交叉检视:过期已久又被补撤销的链接不应再多留一窗——
    撤销/过期任一失效时刻早于窗口即清。"""
    engine = _engine(tmp_path)
    old, recent = _iso_days_ago(200), _iso_days_ago(5)
    with Session(engine) as session:
        # 200 天前过期、5 天前又被撤销:已失效 200 天,必须清。
        session.add(ArticleShareRecord(token="dshr_both", article_id="a1", owner_username="u",
                                       created_at=old, expires_at=old, revoked_at=recent))
        # 刚过期 + 老撤销时间不存在的存活行:保留。
        session.add(ArticleShareRecord(token="dshr_fresh_exp", article_id="a2", owner_username="u",
                                       created_at=old, expires_at=recent))
        session.commit()
    deleted = retention.run_retention_cleanup(engine)
    assert deleted["article_shares"] == 1
    with Session(engine) as session:
        kept = {s.token for s in session.exec(select(ArticleShareRecord)).all()}
        assert kept == {"dshr_fresh_exp"}


def test_retention_still_covers_fetch_runs(tmp_path):
    """扩面不回归：原有纯时间窗表照常清理。"""
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(FetchRunRecord(fetcher_id="f1", started_at=_iso_days_ago(200), status="success"))
        session.add(FetchRunRecord(fetcher_id="f1", started_at=_iso_days_ago(1), status="success"))
        session.commit()
    deleted = retention.run_retention_cleanup(engine)
    assert deleted["fetch_runs"] == 1


# ══ M21 计量唯一约束与原子累加 ═════════════════════════════════════


def test_metering_unique_indexes_enforced(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        session.add(AiUsageRecord(day="2026-09-01", username="a", purpose="ask", model="m",
                                  calls=1, updated_at="t"))
        session.commit()
        session.add(AiUsageRecord(day="2026-09-01", username="a", purpose="ask", model="m",
                                  calls=1, updated_at="t"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(ReaderReadRecord(day="2026-09-01", username="a", source_id="s",
                                     reads=1, updated_at="t"))
        session.commit()
        session.add(ReaderReadRecord(day="2026-09-01", username="a", source_id="s",
                                     reads=1, updated_at="t"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_record_usage_upserts_single_row(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        for _ in range(3):
            ai_usage.record_usage(session, username="alice", purpose="ask", model="m1",
                                  usage={"prompt_tokens": 10, "completion_tokens": 5}, day="2026-09-01")
        rows = session.exec(select(AiUsageRecord)).all()
        assert len(rows) == 1
        assert rows[0].calls == 3
        assert rows[0].prompt_tokens == 30
        assert rows[0].total_tokens == 45


def test_record_read_upserts_single_row(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        for _ in range(4):
            reader_activity.record_read(session, username="bob", source_id="s1", day="2026-09-01")
        rows = session.exec(select(ReaderReadRecord)).all()
        assert len(rows) == 1
        assert rows[0].reads == 4


def test_migration_dedups_existing_duplicate_rows(tmp_path):
    """回放护栏：迁移 a7e2f95c1d40 在建唯一索引前把存量重复行求和合并。"""
    from alembic import command as alembic_command
    from sqlalchemy import create_engine, inspect

    from storage.migrations import make_alembic_config

    db_url = f"sqlite:///{tmp_path / 'dedup.db'}"
    cfg = make_alembic_config(db_url)
    alembic_command.upgrade(cfg, "b4a1c7e5d2f8")  # 停在本迁移的前一版

    engine = create_engine(db_url)
    with engine.begin() as conn:
        for calls, tokens in ((1, 10), (2, 20), (3, 30)):
            conn.execute(text(
                "INSERT INTO ai_usage (day, username, purpose, model, calls, prompt_tokens,"
                " completion_tokens, total_tokens, updated_at)"
                " VALUES ('2026-08-01', 'dup', 'ask', 'm', :c, :t, 0, :t, 'x')"
            ), {"c": calls, "t": tokens})
        conn.execute(text(
            "INSERT INTO ai_usage (day, username, purpose, model, calls, prompt_tokens,"
            " completion_tokens, total_tokens, updated_at)"
            " VALUES ('2026-08-02', 'solo', 'ask', 'm', 7, 70, 0, 70, 'x')"
        ))
        for reads in (2, 5):
            conn.execute(text(
                "INSERT INTO reader_reads (day, username, source_id, reads, updated_at)"
                " VALUES ('2026-08-01', 'dup', 's1', :r, 'x')"
            ), {"r": reads})
    engine.dispose()

    alembic_command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT username, calls, prompt_tokens, total_tokens FROM ai_usage ORDER BY username"
            )).fetchall()
            assert rows == [("dup", 6, 60, 60), ("solo", 7, 70, 70)]
            reads = conn.execute(text("SELECT reads FROM reader_reads")).fetchall()
            assert reads == [(7,)]
            index_names = {ix["name"] for ix in inspect(conn).get_indexes("ai_usage")}
            assert "uq_ai_usage_day_user_purpose_model" in index_names
    finally:
        engine.dispose()


def test_delete_recreate_delete_merges_tombstone(tmp_path):
    """v3.43.1 codex 交叉检视·高:同名账号「删→重建→同维度计量→再删」——
    裸 UPDATE 墓碑化会撞 v3.43 新唯一索引抛 IntegrityError 令删号 500;
    合并式墓碑化应成功且计数守恒。"""
    from services.accounts import delete_user

    engine = _engine(tmp_path)
    day = "2026-09-01"
    with Session(engine) as session:
        create_user(session, "repeat", "pw123456", "user")
        ai_usage.record_usage(session, username="repeat", purpose="ask", model="m",
                              usage={"total_tokens": 10}, day=day)
        reader_activity.record_read(session, username="repeat", source_id="s1", day=day)
        delete_user(session, "repeat")

        create_user(session, "repeat", "pw123456", "user")
        ai_usage.record_usage(session, username="repeat", purpose="ask", model="m",
                              usage={"total_tokens": 25}, day=day)
        reader_activity.record_read(session, username="repeat", source_id="s1", day=day)
        reader_activity.record_read(session, username="repeat", source_id="s1", day=day)
        delete_user(session, "repeat")  # 曾在此抛 UNIQUE constraint failed

        rows = session.exec(select(AiUsageRecord)).all()
        assert len(rows) == 1
        assert rows[0].username == "deleted:repeat"
        assert rows[0].calls == 2 and rows[0].total_tokens == 35
        reads = session.exec(select(ReaderReadRecord)).all()
        assert len(reads) == 1
        assert reads[0].username == "deleted:repeat" and reads[0].reads == 3


def test_metering_upsert_sql_shape(tmp_path):
    """结构性守卫(替代 flaky 的多线程并发测试):record_usage/record_read 必须是
    单条带 ON CONFLICT 的 INSERT,写前不得对本表 SELECT——防止将来悄悄改回
    「先查后插/递增」的竞态形态。"""
    from sqlalchemy import event

    engine = _engine(tmp_path)
    statements = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    with Session(engine) as session:
        statements.clear()
        ai_usage.record_usage(session, username="a", purpose="ask", model="m",
                              usage={"total_tokens": 1}, day="2026-09-01")
        ai_stmts = [s for s in statements if "ai_usage" in s]
        assert len(ai_stmts) == 1 and "ON CONFLICT" in ai_stmts[0] and ai_stmts[0].lstrip().startswith("INSERT")

        statements.clear()
        reader_activity.record_read(session, username="a", source_id="s", day="2026-09-01")
        read_stmts = [s for s in statements if "reader_reads" in s]
        assert len(read_stmts) == 1 and "ON CONFLICT" in read_stmts[0] and read_stmts[0].lstrip().startswith("INSERT")


# ══ 三波补充检视回归(v3.43.2 codex 交叉检视) ══════════════════════


def test_delete_user_clears_username_level_kv(tmp_path):
    """M03 补漏:删号须清用户名级 KV 标记——否则同名重建拿不到默认订阅播种、
    并继承旧身份当日自定源新增额度。同时钉住三处 key 前缀常量与
    delete_user 内字面量的一致性(防漂移)。"""
    from api.app import DEFAULTS_SEEDED_KEY_PREFIX
    from api.routers.feedback import _FEEDBACK_SEEN_KEY_PREFIX
    from services.accounts import delete_user
    from services.user_sources import DAILY_ADD_KEY_PREFIX
    from models.db import AppSettingRecord

    # delete_user 用字面量拼 key,此处以权威常量断言格式仍然吻合。
    assert DEFAULTS_SEEDED_KEY_PREFIX == "reader_defaults_seeded"
    assert _FEEDBACK_SEEN_KEY_PREFIX == "feedback_seen:"
    assert DAILY_ADD_KEY_PREFIX == "user_sources_added:"

    engine = _engine(tmp_path)
    with Session(engine) as session:
        create_user(session, "kvuser", "pw123456", "user")
        create_user(session, "other", "pw123456", "user")
        for key in (
            "reader_defaults_seeded:kvuser",
            "feedback_seen:kvuser",
            "user_sources_added:kvuser:2026-09-01",
            # 他人同前缀 key 不得被误删。
            "reader_defaults_seeded:other",
            "user_sources_added:other:2026-09-01",
        ):
            session.add(AppSettingRecord(key=key, value="x"))
        session.commit()
        delete_user(session, "kvuser")
        remaining = {r.key for r in session.exec(select(AppSettingRecord)).all()}
    assert "reader_defaults_seeded:kvuser" not in remaining
    assert "feedback_seen:kvuser" not in remaining
    assert "user_sources_added:kvuser:2026-09-01" not in remaining
    assert {"reader_defaults_seeded:other", "user_sources_added:other:2026-09-01"} <= remaining


def test_delete_user_kv_like_escapes_wildcards(tmp_path):
    """终审返修:用户名允许下划线,LIKE 的 `_` 通配不转义时「a_b」删号会误删
    「axb」的自定源日计数 KV。"""
    from services.accounts import delete_user
    from models.db import AppSettingRecord

    engine = _engine(tmp_path)
    with Session(engine) as session:
        create_user(session, "a_b", "pw123456", "user")
        create_user(session, "axb", "pw123456", "user")
        session.add(AppSettingRecord(key="user_sources_added:a_b:2026-09-01", value="3"))
        session.add(AppSettingRecord(key="user_sources_added:axb:2026-09-01", value="5"))
        session.commit()
        delete_user(session, "a_b")
        remaining = {r.key for r in session.exec(select(AppSettingRecord)).all()}
    assert "user_sources_added:a_b:2026-09-01" not in remaining
    assert "user_sources_added:axb:2026-09-01" in remaining


def test_cached_ai_paths_skip_cost_gates(tmp_path):
    """M02:配额/预算是成本闸——缓存命中零成本,不得被 pre_llm_check 拦截;
    缓存未命中(将发起真实 LLM 调用)时才执行成本闸。"""
    import asyncio
    import json as jsonlib

    from services import reader_ai
    from storage.impl.db_storage import DatabaseStorage

    sink = DatabaseStorage(db_url=f"sqlite:///{tmp_path / 'ai.db'}")
    from models.db import ArticleRecord

    body = "正文" * 100
    fp = reader_ai._body_fingerprint(body)
    with Session(sink.engine) as session:
        session.add(ArticleRecord(
            id="art1", title="t", content_type="rss_article", source_id="s",
            source_url="https://example.com/a", content=body, has_content=True,
            extensions_json=jsonlib.dumps({
                reader_ai.SUMMARY_KEY: "已缓存摘要", reader_ai.SUMMARY_FP_KEY: fp,
            }),
            publish_date="2026-09-01", fetched_date="2026-09-01",
        ))
        session.commit()

    def _blocked():
        raise RuntimeError("cost gate hit")

    # 缓存命中:成本闸不执行,直接返回缓存。
    result = asyncio.run(reader_ai.summarize_article(
        sink, "art1", None, None, pre_llm_check=_blocked
    ))
    assert result == {"summary": "已缓存摘要", "cached": True}

    # 缓存失配(正文指纹变化):成本闸在 LLM 调用前执行并中止。
    with Session(sink.engine) as session:
        record = session.get(ArticleRecord, "art1")
        record.content = body + "更新"
        session.add(record)
        session.commit()
    with pytest.raises(RuntimeError, match="cost gate hit"):
        asyncio.run(reader_ai.summarize_article(
            sink, "art1", None, None, pre_llm_check=_blocked
        ))


def test_audit_summary_edge_cases(tmp_path):
    """M05/M11 审计摘要边界:批量账户人数按服务层规范化口径(strip/去空/去重)、
    body 缺失记「数量未知」不伪造 0、/api/fetch/batch 走精确规则、
    含斜杠的文章 ID({article_id:path} 路由)不退化为空摘要。"""
    from services.admin_audit import record_audit

    engine = _engine(tmp_path)

    def _summary_of(method, path, body):
        record_audit(engine, username="admin", method=method, path=path,
                     status_code=200, body=body)
        from models.db import AdminAuditRecord
        with Session(engine) as session:
            rows = session.exec(select(AdminAuditRecord)).all()
            return rows[-1].summary

    assert "1 个账户" in _summary_of(
        "POST", "/api/accounts/batch",
        {"usernames": ["alice", " alice ", "alice", ""], "ai_beta_enabled": True},
    )
    assert "数量未知" in _summary_of("POST", "/api/accounts/batch", None)
    assert "数量未知" in _summary_of("POST", "/api/articles/batch-delete", None)
    assert _summary_of("POST", "/api/fetch/batch", {"items": [{}, {}]}) == "批量触发采集 2 个节点"
    assert _summary_of("DELETE", "/api/articles/abc/def", None) == "删除文章 abc/def"


# ══ M16 by_day_user 服务端裁剪 ═════════════════════════════════════


def test_summarize_caps_by_day_user_series(tmp_path):
    engine = _engine(tmp_path)
    today = ai_usage._today()
    with Session(engine) as session:
        for i in range(9):
            ai_usage.record_usage(session, username=f"u{i}", purpose="ask", model="m",
                                  usage={"total_tokens": (i + 1) * 10}, day=today)
        summary = ai_usage.summarize(session, days=7)
    series = {r["username"] for r in summary["by_day_user"]}
    assert len(series) == ai_usage.BY_DAY_USER_TOP_N + 1
    assert ai_usage.OTHER_SERIES_LABEL in series
    # Top 按窗口 total_tokens：u8 最大必在，u0/u1/u2 最小三位并入「其它」。
    assert "u8" in series and "u0" not in series
    # 总量守恒：裁剪只折叠系列，不吞数据。
    assert sum(r["total_tokens"] for r in summary["by_day_user"]) == sum(
        (i + 1) * 10 for i in range(9)
    )
    # by_user 全量榜不受裁剪影响。
    assert len(summary["by_user"]) == 9


def test_summarize_other_username_not_merged_into_bucket(tmp_path):
    """v3.43.1 codex 交叉检视:「其它」是合法用户名——聚合桶键改带冒号 sentinel
    (用户名禁冒号),真实用户「其它」进 Top 时不得与尾部用户合并桶混淆。"""
    engine = _engine(tmp_path)
    today = ai_usage._today()
    with Session(engine) as session:
        # 「其它」是最大用户(必进 Top),另造 7 个用户挤出 1 个进合并桶。
        ai_usage.record_usage(session, username="其它", purpose="ask", model="m",
                              usage={"total_tokens": 100}, day=today)
        for i in range(7):
            ai_usage.record_usage(session, username=f"u{i}", purpose="ask", model="m",
                                  usage={"total_tokens": (i + 1) * 10}, day=today)
        summary = ai_usage.summarize(session, days=7)
    by_name = {r["username"]: r for r in summary["by_day_user"]}
    assert by_name["其它"]["total_tokens"] == 100          # 真实用户系列不被污染
    assert ai_usage.OTHER_SERIES_LABEL in by_name          # 合并桶走 sentinel 键
    assert ":" in ai_usage.OTHER_SERIES_LABEL              # sentinel 永不撞合法用户名
    # 合并桶只含被挤出的尾部用户量(u0+u1 = 10+20)。
    assert by_name[ai_usage.OTHER_SERIES_LABEL]["total_tokens"] == 30


def test_summarize_small_userbase_untrimmed(tmp_path):
    engine = _engine(tmp_path)
    today = ai_usage._today()
    with Session(engine) as session:
        for i in range(3):
            ai_usage.record_usage(session, username=f"u{i}", purpose="ask", model="m",
                                  usage={"total_tokens": 10}, day=today)
        summary = ai_usage.summarize(session, days=7)
    series = {r["username"] for r in summary["by_day_user"]}
    assert series == {"u0", "u1", "u2"}


# ══ M22 单用户最近登录标量查询 ═════════════════════════════════════


def test_last_login_for_user(tmp_path):
    engine = _engine(tmp_path)
    with Session(engine) as session:
        assert last_login_for_user(session, "nobody") is None
        create_user(session, "alice", "pw123456", "user")
        create_user(session, "bob", "pw123456", "user")
        touch_login(session, "alice")
        touch_login(session, "alice")
        touch_login(session, "bob")
        latest = last_login_for_user(session, "alice")
        assert latest is not None
        # 与全表 GROUP BY 口径一致（单人标量查询只是省资源，不改语义）。
        from services.accounts import last_login_by_user
        assert latest == last_login_by_user(session)["alice"]


# ══ M13 source-health 回退只服务无快照节点 ═════════════════════════


def test_source_health_state_path_and_fallback(tmp_path, monkeypatch):
    """有 state 快照的节点走快照；无快照的节点回退运行史聚合（语义不回归）。
    并以 SQL 参数监听钉住收窄本身:fetch_runs 查询参数不得包含有快照的节点
    (否则退回「全量载入」也能通过功能断言,即假绿灯)。"""
    from sqlalchemy import event

    from api.routers import monitoring

    engine = _engine(tmp_path)
    captured = []

    @event.listens_for(engine, "before_cursor_execute")
    def _capture(conn, cursor, statement, parameters, context, executemany):
        if "fetch_runs" in statement and statement.lstrip().startswith("SELECT"):
            captured.append((statement, parameters))
    fetchers = [
        {"id": "with_state", "name": "A", "icon": "", "desc": "", "category": "c", "content_type": "t"},
        {"id": "no_state", "name": "B", "icon": "", "desc": "", "category": "c", "content_type": "t"},
    ]
    monkeypatch.setattr(monitoring.fetcher_registry, "get_all_metadata", lambda: [dict(f) for f in fetchers])
    now = _iso_days_ago(0)
    with Session(engine) as session:
        session.add(SourceStateRecord(
            source_id="with_state", fetcher_id="with_state", content_type="t",
            status="healthy", total_runs=5, success_runs=5,
            last_started_at=now, last_success_at=now, updated_at=now,
        ))
        session.add(FetchRunRecord(fetcher_id="no_state", started_at=now, status="failed",
                                   error_message="boom"))
        session.commit()
        result = monitoring.get_source_health(session=session)
    by_id = {item["source_id"]: item for item in result}
    assert by_id["with_state"]["health_status"] == "healthy"
    assert by_id["with_state"]["total_runs"] == 5
    assert by_id["no_state"]["health_status"] == "failing"
    assert by_id["no_state"]["total_runs"] == 1
    # 收窄守卫:运行史查询只应带无快照节点的 id。
    run_queries = [(s, p) for s, p in captured if "fetcher_id IN" in s]
    assert run_queries, "应存在针对无快照节点的运行史回退查询"
    for _, params in run_queries:
        flat = params if isinstance(params, (list, tuple)) else [params]
        assert "with_state" not in [str(v) for v in flat], "有快照的节点不应进入运行史查询参数"
        assert "no_state" in [str(v) for v in flat]
