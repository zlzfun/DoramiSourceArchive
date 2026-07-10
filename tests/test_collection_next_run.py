"""collection-jobs 序列化的 next_run_at 契约(运行页时刻表倒计时的数据依赖)。

next_run_at = job 级 cron 与 per-node cron 覆盖中最早的下次触发(本地时区 ISO);
停用任务恒为 None;无有效 5 段表达式为 None。解析语义与 app.add_cron_job 一致。
"""

import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from models.db import CollectionJobRecord  # noqa: E402
from api.routers.collection import serialize_collection_job, _next_fire_iso  # noqa: E402


def _job(**overrides):
    now = datetime.datetime.now().isoformat()
    base = dict(
        id=1, name="J", fetcher_ids_json='["fx"]', cron_expr="",
        per_fetcher_cron_json="{}", is_active=True, created_at=now, updated_at=now,
    )
    base.update(overrides)
    return CollectionJobRecord(**base)


def test_next_run_at_from_job_cron():
    payload = serialize_collection_job(_job(cron_expr="0 9 * * *"))
    fire = datetime.datetime.fromisoformat(payload["next_run_at"])
    assert fire.hour == 9 and fire.minute == 0
    assert fire > datetime.datetime.now().astimezone()


def test_next_run_at_takes_earliest_across_per_node_cron():
    # 每分钟触发的节点覆盖必然早于每日 09:00 的任务整体 cron
    payload = serialize_collection_job(
        _job(cron_expr="0 9 * * *", per_fetcher_cron_json='{"fx": "* * * * *"}')
    )
    fire = datetime.datetime.fromisoformat(payload["next_run_at"])
    assert (fire - datetime.datetime.now().astimezone()) <= datetime.timedelta(minutes=1, seconds=5)


def test_next_run_at_none_when_inactive_or_no_cron():
    assert serialize_collection_job(_job(cron_expr="0 9 * * *", is_active=False))["next_run_at"] is None
    assert serialize_collection_job(_job(cron_expr=""))["next_run_at"] is None


def test_next_fire_iso_skips_invalid_exprs():
    assert _next_fire_iso(["not a cron", "1 2 3", "61 9 * * *"]) is None
    # 混入无效表达式不影响有效表达式取值
    assert _next_fire_iso(["bogus", "0 9 * * *"]) is not None
