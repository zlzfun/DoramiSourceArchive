import os
import sys
from argparse import Namespace
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from export_shendeng_daily_news import (  # noqa: E402
    _extract_daily_brief_for_date,
    _extract_items_for_date,
    items_to_shendeng_batch,
    resolve_export_config,
)


def _item(**over):
    base = {
        "title_cn": "中文标题",
        "classification": "学术论文",
        "source": "机器之心",
        "company": "OpenAI",
        "realm": "基础大模型",
        "summary": ["**核心技术**：具体细节", "**第二点**：更多细节"],
        "comment": "硬核点评",
        "source_url": "https://example.test/a",
        "publish_date": "2026-06-05T08:00:00",
    }
    base.update(over)
    return base


def test_basic_field_mapping():
    [entry] = items_to_shendeng_batch([_item()])
    assert entry["title"] == "中文标题"
    assert entry["classification"] == "学术论文"
    assert entry["type"] == entry["classification"]  # type == classification
    assert entry["source"] == "机器之心"
    assert entry["realm"] == "基础大模型"
    assert entry["summary"] == ""  # 固定空字符串
    assert entry["link"] == "https://example.test/a"
    assert entry["content"] == "• **核心技术**：具体细节\n• **第二点**：更多细节"  # • 前缀
    assert entry["comment"] == "硬核点评"
    assert entry["sort"] == 1
    assert entry["time"] == "2026-06-05"  # publish_date[:10]
    assert entry["status"] == "published"
    assert entry["company"] == "OpenAI"


def test_company_omitted_when_empty():
    [entry] = items_to_shendeng_batch([_item(company="")])
    assert "company" not in entry  # 非空才加键
    [entry2] = items_to_shendeng_batch([_item(company="   ")])
    assert "company" not in entry2


def test_empty_summary_fallback():
    [entry] = items_to_shendeng_batch([_item(summary=[])])
    assert entry["content"] == "暂无详情"


def test_sort_increments_and_fallbacks():
    batch = items_to_shendeng_batch([
        _item(title_cn="", classification="", source="", realm="", publish_date=""),
        _item(),
    ])
    assert [e["sort"] for e in batch] == [1, 2]
    # 各字段兜底
    assert batch[0]["title"] == "无标题"
    assert batch[0]["classification"] == "产业资讯"
    assert batch[0]["type"] == "产业资讯"
    assert batch[0]["source"] == "未知来源"
    assert batch[0]["realm"] == "综合动态"
    # publish_date 为空 → time 兜底今天
    assert batch[0]["time"] == datetime.now().strftime("%Y-%m-%d")


def test_extract_items_from_article_record_shape():
    records = [
        {"id": "daily_brief_2026-06-07", "publish_date": "2026-06-07",
         "extensions_json": '{"items": [{"title_cn": "x"}]}'},
        {"id": "daily_brief_2026-06-06", "publish_date": "2026-06-06", "extensions_json": "{}"},
    ]
    items = _extract_items_for_date(records, "2026-06-07")
    assert items == [{"title_cn": "x"}]


def test_extract_daily_brief_includes_markdown_content():
    records = [
        {"id": "daily_brief_2026-06-07", "publish_date": "2026-06-07", "content": "# 日报正文",
         "extensions_json": '{"items": [{"title_cn": "x"}]}'},
    ]
    brief = _extract_daily_brief_for_date(records, "2026-06-07")
    assert brief["items"] == [{"title_cn": "x"}]
    assert brief["content"] == "# 日报正文"


def test_extract_items_from_feed_shape():
    records = [
        {"id": "daily_brief_2026-06-07",
         "metadata": {"publish_date": "2026-06-07", "extensions": {"items": [{"title_cn": "y"}]}}},
    ]
    items = _extract_items_for_date(records, "2026-06-07")
    assert items == [{"title_cn": "y"}]


def test_extract_items_missing_date_returns_none():
    records = [{"id": "daily_brief_2026-06-06", "publish_date": "2026-06-06", "extensions_json": "{}"}]
    assert _extract_items_for_date(records, "2026-06-07") is None


def test_resolve_export_config_uses_cli_overrides_and_formats_outputs(monkeypatch):
    for env_name in [
        "DORAMI_BASE_URL",
        "DORAMI_DAILY_BRIEF_DATE",
        "SHENDENG_EXPORT_OUTPUT",
        "SHENDENG_EXPORT_MARKDOWN_OUTPUT",
        "DORAMI_FEED_TOKEN",
        "DORAMI_ADMIN_USER",
        "DORAMI_ADMIN_PASSWORD",
    ]:
        monkeypatch.delenv(env_name, raising=False)

    args = Namespace(
        base_url="https://www.dorami.cloud",
        date="2026-06-15",
        username=None,
        password=None,
        feed_token="dfeed_test",
        output="out-{date}.json",
        markdown_output="brief-{date}.md",
    )

    cfg = resolve_export_config(args)

    assert cfg["base_url"] == "https://www.dorami.cloud"
    assert cfg["date"] == "2026-06-15"
    assert cfg["output"] == "out-2026-06-15.json"
    assert cfg["markdown_output"] == "brief-2026-06-15.md"
    assert cfg["feed_token"] == "dfeed_test"
