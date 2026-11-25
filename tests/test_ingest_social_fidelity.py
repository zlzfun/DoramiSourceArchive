"""收录推送:推文结构的保真与补全(issue #341)。

推送的推文要能还原成与抓取器入库的同一条一样的形状(引用帖、被转推的原帖、配图),没被采用的字段要在回执里
点出来;同一条再次到达时,收录时不完整的条目能被补全。

issue #344 起令牌只能推它绑定的源、不再建源:这里的用例经管理员「单条收录」进来(同一个入口、同一套载荷整理
与存储路径);令牌推送的出处解析见 ``tests/test_ingest_bindings.py``。
"""

import asyncio
import copy
import json
import os
import sys

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tests.test_ingest_push import (  # noqa: E402
    _bind, _collect, _issue_token, _login, _push, _register, _row, _setup,
)

AVATAR = "https://pbs.twimg.com/profile_images/1/lab_normal.jpg"
AVATAR_LARGE = "https://pbs.twimg.com/profile_images/1/lab_400x400.jpg"
OTHER_AVATAR = "https://pbs.twimg.com/profile_images/2/other_normal.jpg"
IMG_A = "https://pbs.twimg.com/media/A.jpg"
IMG_B = "https://pbs.twimg.com/media/B.jpg"
IMG_Q = "https://pbs.twimg.com/media/Q.jpg"
# 抓取器不带、只属于「怎么来的」的键:推送不落平台原始数据,也不收互动数字。
NOT_CARRIED = ("raw_data", "metrics")


def _x_payload(relation="quoted", *, handle="new_lab", user_id="9001", post_id="2001"):
    """X API v2 一页时间线里的一条帖子:带一张图,引用 / 转推另一条带图的帖子。"""
    text = "Big news 👇 https://t.co/q" if relation == "quoted" else "RT @other: the original post, in full https://t…"
    return {
        "data": {
            "id": post_id,
            "author_id": user_id,
            "text": text,
            "lang": "en",
            "created_at": "2026-11-20T08:00:00.000Z",
            "conversation_id": post_id,
            "attachments": {"media_keys": ["3_a"]} if relation == "quoted" else {},
            "referenced_tweets": [{"type": relation, "id": "1900"}],
            "entities": {"hashtags": [{"tag": "AI"}]},
            "public_metrics": {"like_count": 7, "retweet_count": 2},
        },
        "includes": {
            "users": [
                {"id": user_id, "username": handle, "name": "New Lab", "profile_image_url": AVATAR},
                {"id": "42", "username": "other", "name": "Other", "profile_image_url": OTHER_AVATAR},
            ],
            "tweets": [{
                "id": "1900", "author_id": "42", "lang": "en",
                "text": "the original post, in full https://t.co/m",
                "attachments": {"media_keys": ["3_q"]},
            }],
            "media": [
                {"media_key": "3_a", "type": "photo", "url": IMG_A},
                {"media_key": "3_q", "type": "photo", "url": IMG_Q},
            ],
        },
    }


def _fetched(sink, payload, source_id):
    """抓取器对同一条帖子产出的内容对象。"""
    from config import XApiConfig
    from fetchers.impl.x_timeline_fetcher import XTimelineFetcher

    config = XApiConfig(
        bearer_token="t", base_url="https://api.x.test/2", timeout_seconds=5, max_results=5,
        monthly_budget_usd=5.0,
    )
    author = payload["includes"]["users"][0]
    item = XTimelineFetcher(x_config=config, runtime_engine=sink.engine)._content_for_post(
        payload["data"],
        runtime_source_id=source_id,
        fallback_handle=author["username"],
        fallback_user=author,
        payload=payload,
    )
    item.source_id = source_id
    return item


class _OneShotFetcher:
    """只产出给定条目的抓取器替身,用来让条目走一遍真实的抓取管线。"""

    def __init__(self, source_id, items):
        self.source_id = source_id
        self._items = items

    async def fetch(self, **_kwargs):
        for item in self._items:
            yield item


def _run_pipeline(sink, source_id, items):
    from pipeline.core import DataPipeline

    return asyncio.run(DataPipeline([sink]).run_task(_OneShotFetcher(source_id, items)))


def _stored(sink, article_id):
    from models.db import ArticleRecord

    row = _row(sink.engine, ArticleRecord, article_id)
    return row, json.loads(row.extensions_json or "{}")


def _reader_summary(sink, article_id):
    """读者列表里这一条的摘要(列表项直接投影 summary_zh,不看指纹)。"""
    from api.articles_view import serialize_article_list_item
    from models.db import ArticleRecord

    return serialize_article_list_item(_row(sink.engine, ArticleRecord, article_id))["summary_zh"]


def _set_extension(sink, article_id, key, value):
    from models.db import ArticleRecord

    with Session(sink.engine) as session:
        row = session.get(ArticleRecord, article_id)
        extensions = json.loads(row.extensions_json or "{}")
        extensions[key] = value
        row.extensions_json = json.dumps(extensions, ensure_ascii=False)
        session.add(row)
        session.commit()


def _sender(client, channel, *, bind=(), register=None):
    """按通道给出发送函数与入库后的(通道,收录者)。

    ``manual`` 是管理员「单条收录」;``push`` 是收录令牌——先登记(可选)、绑定,再用令牌推
    (issue #344:令牌只能推它绑定的源)。两条路走同一套载荷整理与存储,补全规则应当一样。
    """
    if channel == "manual":
        return (lambda items: _collect(client, items)), ("manual", "admin")
    sources = list(bind)
    if register:
        sources.append(_register(client, **register))
    token = _issue_token(client)
    _bind(client, token, *sources)
    return (lambda items: _push(client, token, items)), ("push", "research-agent")


def _structured(relation="quoted", **overrides):
    """与 ``_x_payload`` 同一条帖子,用结构化字段描述。"""
    reference = {
        "url": "https://x.com/other/status/1900",
        "content": "the original post, in full https://t.co/m",
        "lang": "en",
        "author": {"handle": "other", "name": "Other", "avatar_url": OTHER_AVATAR},
        "media_urls": [IMG_Q],
    }
    item = {
        "kind": "social_post",
        "post_id": "2001",
        "content": "Big news 👇 https://t.co/q" if relation == "quoted" else "",
        "publish_date": "2026-11-20T08:00:00.000Z",
        "lang": "en",
        "author": {"handle": "new_lab", "user_id": "9001", "name": "New Lab", "avatar_url": AVATAR},
        "media_urls": [IMG_A] if relation == "quoted" else [],
        relation: reference,
    }
    item.update(overrides)
    return item


def test_structured_push_keeps_the_quote_the_repost_and_the_images(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [
            _structured("quoted"),
            _structured("reposted", post_id="2002"),
        ]).json()["results"]
    assert [r["status"] for r in results] == ["created", "created"], results
    assert all("warnings" not in r for r in results), results

    row, ext = _stored(sink, "x_new_lab_2001")
    assert row.content == "Big news 👇 https://t.co/q"
    assert row.source_url == "https://x.com/new_lab/status/2001"
    assert ext["media_urls"] == [IMG_A]  # 引用帖的图只在 quoted 里
    assert ext["quoted"] == {
        "author_name": "Other",
        "author_handle": "other",
        "author_avatar_url": OTHER_AVATAR,
        "author_avatar_url_large": OTHER_AVATAR.replace("_normal", "_400x400"),
        "text": "the original post, in full https://t.co/m",
        "url": "https://x.com/other/status/1900",
        "media_urls": [IMG_Q],
        "lang": "en",
    }
    assert ext["quoted_post_id"] == "1900" and "reposted" not in ext
    assert (ext["author_name"], ext["author_avatar_url"], ext["author_avatar_url_large"]) == (
        "New Lab", AVATAR, AVATAR_LARGE,
    )
    assert ext["raw_data"] == {} and ext["metrics"] == {}

    row, ext = _stored(sink, "x_new_lab_2002")
    # 纯转推没有自己的正文:按平台惯例补成「RT @原作者: 原文」,原帖的图并进顶层。
    assert row.content == "RT @other: the original post, in full https://t.co/m" and row.has_content
    assert ext["reposted"]["author_handle"] == "other" and ext["reposted_post_id"] == "1900"
    assert ext["media_urls"] == [IMG_Q] and "quoted" not in ext


@pytest.mark.parametrize("relation", ["quoted", "retweeted"])
def test_raw_push_stores_what_the_fetcher_would(monkeypatch, tmp_path, relation):
    app_module, sink = _setup(monkeypatch, tmp_path)
    payload = _x_payload(relation)
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [{"kind": "social_post", "raw": payload}]).json()["results"]
    assert results[0]["status"] == "created" and "warnings" not in results[0], results
    assert results[0]["source_id"] == "x_new_lab"

    from models.content import serialize_to_metadata

    native = _fetched(sink, payload, "x_new_lab")
    native_ext = serialize_to_metadata(native)["extensions"]
    row, ext = _stored(sink, results[0]["id"])
    assert (row.id, row.title, row.content, row.source_url, row.publish_date) == (
        native.id, native.title, native.content, native.source_url, native.publish_date,
    )
    assert {k: v for k, v in ext.items() if k not in NOT_CARRIED} == {
        k: v for k, v in native_ext.items() if k not in NOT_CARRIED
    }
    assert ("quoted" in ext, "reposted" in ext) == (relation == "quoted", relation == "retweeted")
    # 调用方给的原始 JSON 与互动数字不落库:社交回填会把 raw_data 当平台事实重放。
    assert ext["raw_data"] == {} and ext["metrics"] == {}
    assert native_ext["metrics"] and native_ext["raw_data"]


@pytest.mark.parametrize("relation,native_relation", [("quoted", "quoted"), ("reposted", "retweeted")])
def test_structured_push_matches_the_fetcher_on_what_the_card_reads(
    monkeypatch, tmp_path, relation, native_relation
):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [_structured(relation)]).json()["results"]
    assert results[0]["status"] == "created", results

    from models.content import serialize_to_metadata

    native_ext = serialize_to_metadata(_fetched(sink, _x_payload(native_relation), "x_new_lab"))["extensions"]
    _row_, ext = _stored(sink, results[0]["id"])
    card_keys = (
        "author_id", "author_handle", "author_name", "author_avatar_url", "author_avatar_url_large",
        "post_id", "quoted_post_id", "reposted_post_id", "lang", "media_urls", "quoted", "reposted",
    )
    assert {k: ext.get(k) for k in card_keys} == {k: native_ext.get(k) for k in card_keys}


def test_whatever_is_not_used_shows_up_in_the_receipt(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    bad_links = _x_payload("quoted", post_id="7")
    bad_links["includes"]["users"][0]["profile_image_url"] = "javascript:alert(1)"
    bad_links["includes"]["media"][0]["url"] = "ftp://bad/pic.jpg"
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [
            # 0:没问题的条目没有 warnings;旧调用方把配图放在 extensions 里照样生效。
            {"kind": "social_post", "post_id": "1", "content": "ok", "author": {"handle": "new_lab"},
             "extensions": {"media_urls": [IMG_A], "lang": "en", "tags": ["ai"]}},
            # 1:模型不认识的字段(顶层与嵌套)。
            {"kind": "social_post", "post_id": "2", "content": "x", "likes": 3,
             "author": {"handle": "new_lab", "followers": 10},
             "quoted": {"content": "q", "text": "旧形状的键", "author": {"handle": "other", "verified": True}}},
            # 2:写进 extensions 的顶层字段与白名单之外的键。
            {"kind": "social_post", "post_id": "3", "content": "x", "author": {"handle": "new_lab"},
             "extensions": {"quoted": {"content": "q"}, "metrics": {"like_count": 1}}},
            # 3:别的类型的字段。
            {"kind": "article", "url": "https://blog.example.com/p/1", "title": "t", "content": "正文",
             "media_urls": [IMG_A], "author": {"name": "A"}, "audio_url": "https://cdn.example.com/a.mp3"},
            # 4:不是 http(s) 的链接。
            {"kind": "social_post", "post_id": "5", "content": "x",
             "author": {"handle": "new_lab", "avatar_url": "javascript:alert(1)"},
             "media_urls": [IMG_A, "data:image/png;base64,AAAA", "ftp://host/a.png"],
             "quoted": {"content": "q", "url": "javascript:alert(1)"}},
            # 5:给了 raw 又给了结构化字段。
            {"kind": "social_post", "raw": _x_payload("quoted", post_id="6"), "content": "别的正文",
             "media_urls": [IMG_B]},
            # 6:raw 里不是 http(s) 的头像与配图(归一化会丢掉它们)。
            {"kind": "social_post", "raw": bad_links},
        ]).json()["results"]
    assert [r["status"] for r in results] == ["created"] * 7, results
    assert "warnings" not in results[0]
    assert _stored(sink, "x_new_lab_1")[1]["media_urls"] == [IMG_A]
    assert results[1]["warnings"] == [
        "未识别的字段:likes、author.followers、quoted.text、quoted.author.verified(已忽略)",
    ]
    assert results[2]["warnings"] == [
        "这些字段要放在条目顶层,不在 extensions 里:quoted",
        "extensions 里未采用的键:metrics(已忽略)",
    ]
    assert "quoted" not in _stored(sink, "x_new_lab_3")[1]
    assert results[3]["warnings"] == ["文章不使用的字段:author、media_urls、audio_url(已忽略)"]
    assert results[4]["warnings"] == [
        "author.avatar_url 不是 http(s) 链接,已忽略",
        "quoted.url 不是 http(s) 链接,已忽略",
        "media_urls 里有 2 项不是 http(s) 链接,已忽略",
    ]
    _row_, ext = _stored(sink, "x_new_lab_5")
    assert ext["media_urls"] == [IMG_A] and ext["author_avatar_url"] == "" and ext["quoted"]["url"] == ""
    assert results[5]["warnings"] == ["已给 raw,这些字段以 raw 为准:content、media_urls"]
    row, ext = _stored(sink, "x_new_lab_6")
    assert row.content == "Big news 👇 https://t.co/q" and ext["media_urls"] == [IMG_A]
    assert results[6]["warnings"] == [
        "raw.includes.media 里有 1 项的链接不是 http(s),已忽略",
        "raw.includes.users 里有 1 个头像不是 http(s) 链接,已忽略",
    ]
    _row_, ext = _stored(sink, "x_new_lab_7")
    assert ext["media_urls"] == [] and ext["author_avatar_url"] == "" and ext["quoted"]["media_urls"] == [IMG_Q]


def test_wrongly_shaped_new_fields_do_not_fail_the_batch(monkeypatch, tmp_path):
    """这些字段此前不在请求模型里、被整个忽略;旧调用方带着形状不对的值也不能因此整批 422。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        response = _collect(client, [
            {"kind": "social_post", "post_id": "1", "content": "x", "author": {"handle": "new_lab"},
             "quoted": "https://x.com/other/status/1900", "reposted": None, "media_urls": 5, "lang": None,
             "raw": None},
            {"kind": "social_post", "post_id": "2", "content": "x", "author": {"handle": "new_lab"},
             "media_urls": IMG_A, "lang": 7, "quoted": {"content": "q", "author": "other", "media_urls": {"a": 1}}},
            {"kind": "social_post", "post_id": "3", "content": "x", "author": {"handle": "new_lab"},
             "raw": "not an object"},
        ])
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [r["status"] for r in results] == ["created", "created", "rejected"], results
    assert results[0]["warnings"] == ["quoted 需为对象,已忽略", "media_urls 需为字符串列表,已忽略"]
    assert results[1]["warnings"] == ["quoted.author 需为对象,已忽略", "quoted.media_urls 需为字符串列表,已忽略"]
    _row_, ext = _stored(sink, "x_new_lab_2")
    assert (ext["media_urls"], ext["lang"], ext["quoted"]["text"]) == ([IMG_A], "7", "q")
    assert results[2]["reason"] == 'raw 需为 {"data": {…}, "includes": {…}} 形态的 X API 帖子对象'


def test_a_post_link_alone_names_the_author_and_the_post(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [
            {"kind": "social_post", "url": "https://x.com/new_lab/status/777", "content": "只有链接",
             "quoted": {"url": "https://twitter.com/Other/status/1900", "content": "q"}},
        ]).json()["results"]
    assert results[0]["status"] == "created" and results[0]["id"] == "x_new_lab_777", results
    row, ext = _stored(sink, "x_new_lab_777")
    assert row.source_url == "https://x.com/new_lab/status/777"
    assert (ext["author_handle"], ext["post_id"], ext["quoted_post_id"]) == ("new_lab", "777", "1900")
    assert ext["quoted"]["author_handle"] == "Other"


@pytest.mark.parametrize("channel", ["manual", "push"])
def test_pushing_again_fills_what_is_missing_and_overwrites_nothing(monkeypatch, tmp_path, channel):
    app_module, sink = _setup(monkeypatch, tmp_path)
    queued, prefetched = [], []
    monkeypatch.setattr(app_module, "queue_article_analysis_after_commit", lambda ids: queued.append(list(ids)))
    monkeypatch.setattr(app_module, "schedule_media_prefetch", lambda ids: prefetched.append(list(ids)))
    bare = {"kind": "social_post", "post_id": "2001", "content": "第一次推的正文",
            "author": {"handle": "new_lab"}, "extensions": {"media_urls": [IMG_A]}}
    full = _structured("quoted", content="第二次推的正文", media_urls=[IMG_B])
    with TestClient(app_module.app) as client:
        _login(client)
        send, provenance = _sender(client, channel, register={
            "kind": "social_post", "name": "X · New Lab", "handle": "new_lab", "user_id": "9001", "listed": False,
        })
        assert send([bare]).json()["results"][0]["status"] == "created"
        # 服务端写的键(译文缓存)不属于社交结构,补全时原样保留。
        _set_extension(sink, "x_new_lab_2001", "translation_zh_social", {"text": "译文", "fp": "x", "v": 1})
        before, _ext = _stored(sink, "x_new_lab_2001")
        second = send([full]).json()["results"][0]
        third = send([full]).json()["results"][0]
    assert (second["status"], second["id"], second["source_created"]) == ("refreshed", "x_new_lab_2001", False)
    assert third["status"] == "duplicate"

    row, ext = _stored(sink, "x_new_lab_2001")
    assert row.content == "第一次推的正文" and row.title == before.title
    assert ext["media_urls"] == [IMG_A]  # 已有的不换
    assert ext["quoted"]["text"] == "the original post, in full https://t.co/m" and ext["quoted_post_id"] == "1900"
    assert (ext["author_name"], ext["author_id"], ext["author_avatar_url_large"], ext["lang"]) == (
        "New Lab", "9001", AVATAR_LARGE, "en",
    )
    assert ext["translation_zh_social"] == {"text": "译文", "fp": "x", "v": 1}
    # 修订号前进(内网据此同步到),到达时间与来路不变。
    assert row.archive_updated_at > before.archive_updated_at
    assert (row.fetched_date, row.ingest_channel, row.ingest_actor) == (before.fetched_date, *provenance)
    # 新建与被补全的都触发分析入队与配图预取;第三次什么都没变,不触发。
    assert queued == [["x_new_lab_2001"], ["x_new_lab_2001"]] == prefetched


CACHES = {
    "summary_zh": "按收录时的正文生成的摘要",  # 没有指纹的旧缓存:指纹校验拦不住它
    "translation_zh": "正文译文", "translation_zh_fp": "fp",
    "translation_zh_social": {"text": "主推译文", "fp": "x", "v": 1},
}


@pytest.mark.parametrize("channel", ["manual", "push"])
@pytest.mark.parametrize("pushed_text", ["Agent 自己整理的一句话", None])
def test_the_fetched_version_replaces_an_ingested_post(monkeypatch, tmp_path, pushed_text, channel):
    """``pushed_text=None``:收录时的正文与平台原文相同(只有结构不全)。"""
    from storage.impl import db_storage

    app_module, sink = _setup(monkeypatch, tmp_path)
    payload = _x_payload("retweeted", handle="OpenAI", user_id="4398626122", post_id="555")
    same_text = pushed_text is None
    with TestClient(app_module.app) as client:
        _login(client)
        send, provenance = _sender(client, channel, bind=["x_openai"])
        pushed = send([
            {"kind": "social_post", "post_id": "555", "content": pushed_text or payload["data"]["text"],
             "author": {"handle": "OpenAI"}, "extensions": {"tags": ["agent-tag"]},
             "quoted": {"content": "Agent 以为这是引用", "author": {"handle": "someone"}}},
        ]).json()["results"][0]
        assert (pushed["status"], pushed["id"], pushed["source_id"]) == ("created", "x_openai_555", "x_openai")
        for key, value in CACHES.items():
            _set_extension(sink, "x_openai_555", key, value)
        assert _reader_summary(sink, "x_openai_555") == CACHES["summary_zh"]
        before, _ext = _stored(sink, "x_openai_555")

        native = _fetched(sink, payload, "x_openai")
        # 走真实的抓取管线:补全算「已入库」,管线据此把它交给分析入队与配图预取。
        result = _run_pipeline(sink, "x_openai", [native])
        assert (result.saved_count, result.saved_content_ids) == (1, ["x_openai_555"])
        row, ext = _stored(sink, "x_openai_555")
        # 抓取版为准:正文、标题、发布时间与社交结构都换成平台原样;收录时写错的引用帖去掉。
        assert (row.content, row.title, row.publish_date) == (native.content, native.title, native.publish_date)
        assert ext["reposted"]["author_handle"] == "other" and "quoted" not in ext
        assert ext["media_urls"] == [IMG_Q] and ext["tags"] == ["AI"]
        assert ext["raw_data"]["data"]["id"] == "555" and ext["metrics"] == {"like_count": 7, "retweet_count": 2}
        # 正文换了:与正文绑定的缓存一并去掉,读者列表不再给出按旧正文生成的摘要;正文没换就都留着。
        assert {key: ext.get(key) for key in CACHES} == (CACHES if same_text else dict.fromkeys(CACHES))
        assert _reader_summary(sink, "x_openai_555") == (CACHES["summary_zh"] if same_text else None)
        assert row.archive_updated_at > before.archive_updated_at
        assert (row.fetched_date, row.ingest_channel) == (before.fetched_date, provenance[0])

        # 已是抓取版:再抓一次(互动数字变了)按重复处理,推送也不再改它。
        again = copy.deepcopy(payload)
        again["data"]["public_metrics"]["like_count"] = 99
        assert asyncio.run(sink.save_with_outcome(_fetched(sink, again, "x_openai"))) == db_storage.SAVE_DUPLICATE
        repush = send([
            {"kind": "social_post", "post_id": "555", "content": "x", "author": {"handle": "OpenAI"},
             "quoted": {"content": "再推一个引用"}},
        ]).json()["results"][0]
    assert repush["status"] == "duplicate"
    assert _stored(sink, "x_openai_555")[1] == ext


def test_filling_in_the_text_later_keeps_the_structure(monkeypatch, tmp_path):
    """先推了没有正文、但带引用帖与配图的一条,之后只补正文:结构不能被冲掉(检视 R1-2)。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    structure_only = _structured("quoted", content="")
    with TestClient(app_module.app) as client:
        _login(client)
        assert _collect(client, [structure_only]).json()["results"][0]["status"] == "created"
        _set_extension(sink, "x_new_lab_2001", "summary_zh", "没有正文时不该有的摘要")
        before, before_ext = _stored(sink, "x_new_lab_2001")
        assert not before.has_content and before_ext["quoted"] and before_ext["media_urls"] == [IMG_A]
        text_only = {"kind": "social_post", "post_id": "2001", "content": "后来补上的正文\n第二行",
                     "author": {"handle": "new_lab"}}
        second = _collect(client, [text_only]).json()["results"][0]
        third = _collect(client, [{**text_only, "content": "再推一个不同的正文"}]).json()["results"][0]
    assert (second["status"], third["status"]) == ("refreshed", "duplicate")
    row, ext = _stored(sink, "x_new_lab_2001")
    assert (row.content, row.title, row.has_content) == ("后来补上的正文\n第二行", "后来补上的正文", True)
    assert ext["quoted"] == before_ext["quoted"] and ext["media_urls"] == [IMG_A]
    assert ext["author_avatar_url_large"] == AVATAR_LARGE and "summary_zh" not in ext
    assert (row.fetched_date, row.publish_date) == (before.fetched_date, before.publish_date)
    assert row.archive_updated_at > before.archive_updated_at


def test_the_fetched_version_replaces_an_ingested_post_without_text(monkeypatch, tmp_path):
    from storage.impl import db_storage

    app_module, sink = _setup(monkeypatch, tmp_path)
    payload = _x_payload("quoted", handle="OpenAI", user_id="4398626122", post_id="557")
    with TestClient(app_module.app) as client:
        _login(client)
        pushed = _collect(client, [
            {"kind": "social_post", "post_id": "557", "author": {"handle": "OpenAI"}, "media_urls": [IMG_B]},
        ]).json()["results"][0]
        assert pushed["status"] == "created"
        before, _ext = _stored(sink, "x_openai_557")
        assert not before.has_content
        native = _fetched(sink, payload, "x_openai")
        assert asyncio.run(sink.save_with_outcome(native)) == db_storage.SAVE_STRUCTURE_FILLED
    row, ext = _stored(sink, "x_openai_557")
    assert (row.content, row.title, row.has_content) == (native.content, native.title, True)
    assert ext["media_urls"] == [IMG_A] and ext["quoted"]["author_handle"] == "other"
    assert ext["raw_data"]["data"]["id"] == "557"
    # 走的是社交补全而不是通用的补正文:到达时间不动。
    assert (row.fetched_date, row.ingest_channel) == (before.fetched_date, "manual")


def test_an_unusable_author_rejects_only_that_item(monkeypatch, tmp_path):
    """清洗后为空的账号名、raw 里不是字符串的作者字段:只拒收这一条,同批其它条目照常(检视 R1-1)。"""
    app_module, sink = _setup(monkeypatch, tmp_path)
    object_username = _x_payload("quoted", post_id="2010")
    object_username["includes"]["users"][0]["username"] = {"@": []}
    with TestClient(app_module.app) as client:
        _login(client)
        response = _collect(client, [
            {"kind": "social_post", "raw": object_username},
            {"kind": "social_post", "post_id": "1", "content": "x", "author": {"handle": "!!!"}},
            {"kind": "social_post", "raw": {"data": {"id": {"n": 1}, "text": "x"}}, "author": {"handle": "new_lab"}},
            {"kind": "social_post", "post_id": "2", "content": "正常的一条", "author": {"handle": "new_lab"}},
        ])
    assert response.status_code == 200, response.text
    results = response.json()["results"]
    assert [(r["status"], r.get("reason")) for r in results[:3]] == [
        ("rejected", "raw.includes.users 里作者的 username 需为字符串"),
        ("rejected", "缺少作者 handle,无法确定来源"),
        ("rejected", "raw.data 缺少帖子 id"),
    ], results
    assert (results[3]["status"], results[3]["id"]) == ("created", "x_new_lab_2")


@pytest.mark.parametrize("with_platform_raw", [True, False])
def test_a_push_never_rewrites_a_fetched_post(monkeypatch, tmp_path, with_platform_raw):
    from storage.impl import db_storage

    app_module, sink = _setup(monkeypatch, tmp_path)
    payload = _x_payload("quoted", handle="OpenAI", user_id="4398626122", post_id="556")
    payload["data"].pop("attachments")
    payload["data"].pop("referenced_tweets")  # 抓取版本身没有配图与引用
    fetched = _fetched(sink, payload, "x_openai")
    if not with_platform_raw:
        # 不带平台原始数据的抓取条目(别的平台的抓取器、历史数据)同样不被推送改写:看的是来路。
        fetched.raw_data = {}
    with TestClient(app_module.app) as client:
        _login(client)
        assert asyncio.run(sink.save_with_outcome(fetched)) == db_storage.SAVE_CREATED
        before, before_ext = _stored(sink, "x_openai_556")
        result = _collect(client, [
            {"kind": "social_post", "post_id": "556", "content": "x", "author": {"handle": "OpenAI"},
             "media_urls": [IMG_B], "quoted": {"content": "推送想加的引用"}},
        ]).json()["results"][0]
    assert result["status"] == "duplicate"
    after, after_ext = _stored(sink, "x_openai_556")
    assert after_ext == before_ext and after.archive_updated_at == before.archive_updated_at


def test_a_bad_social_payload_rejects_only_that_item(monkeypatch, tmp_path):
    app_module, sink = _setup(monkeypatch, tmp_path)
    no_id = _x_payload("quoted")
    no_id["data"].pop("id")
    with TestClient(app_module.app) as client:
        _login(client)
        results = _collect(client, [
            {"kind": "social_post", "raw": _x_payload("quoted"), "post_id": "9999"},
            {"kind": "social_post", "raw": no_id},
            {"kind": "social_post", "raw": {"data": ["not", "a", "post"]}},
            {"kind": "social_post", "raw": {"data": {"id": "1", "referenced_tweets": 7}}, "author": {"handle": "a"}},
            {"kind": "social_post", "raw": _x_payload("quoted"), "platform": "weibo"},
            {"kind": "social_post", "post_id": "8", "content": "x", "author": {"handle": "new_lab"},
             "quoted": {"content": "长" * 25_001}},
            {"kind": "social_post", "raw": _x_payload("quoted", post_id="2009")},
        ]).json()["results"]
    assert [(r["status"], r.get("reason")) for r in results[:6]] == [
        ("rejected", "post_id 与 raw.data.id 不一致"),
        ("rejected", "raw.data 缺少帖子 id"),
        ("rejected", 'raw 需为 {"data": {…}, "includes": {…}} 形态的 X API 帖子对象'),
        ("rejected", 'raw 需为 {"data": {…}, "includes": {…}} 形态的 X API 帖子对象'),
        ("rejected", "raw 只支持 X 的帖子对象"),
        ("rejected", "quoted.content 过长(上限 25000 字)"),
    ], results
    assert (results[6]["status"], results[6]["id"]) == ("created", "x_new_lab_2009")


def test_merge_rules_leave_complete_rows_and_existing_values_alone():
    from services import social_merge

    thin = {"author_handle": "a", "media_urls": [], "quoted": {"url": "https://x.com/b/status/1"}, "lang": ""}
    rich = {"author_handle": "zzz", "media_urls": [IMG_A], "quoted": {"text": "另一条"}, "lang": "en",
            "metrics": {"like_count": 1}, "raw_data": {"data": {"id": "1"}}}
    merged = social_merge.fill_missing(thin, rich)
    # 只补空的;已有的引用对象整个保留,不与来者拼合;互动数字与原始数据不在可补之列。
    assert merged == {**thin, "media_urls": [IMG_A], "lang": "en"}
    assert social_merge.fill_missing(merged, rich) is None

    # 旧导入入口写过 raw_data.import_source_id,那不是平台原始数据。
    assert not social_merge.has_platform_raw({"raw_data": {"import_source_id": "x"}})
    assert not social_merge.has_platform_raw({"raw_data": {"data": {}}})
    assert social_merge.has_platform_raw({"raw_data": {"data": {"id": "1"}, "includes": {}}})

    adopted = social_merge.adopt_fetched(
        {"quoted": {"text": "收录时写的"}, "summary_zh": "缓存", "tags": ["agent"]},
        {"tags": [], "media_urls": [IMG_A], "reposted": {"text": "原帖"}},
    )
    assert adopted == {"summary_zh": "缓存", "tags": [], "media_urls": [IMG_A], "reposted": {"text": "原帖"}}
