import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fetchers.impl.github_release_fetcher import (
    GenericGitHubReleasesFetcher,
    OpenClawGitHubReleasesFetcher,
    OpenCodeGitHubReleasesFetcher,
)


class DummyJsonResponse:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


RELEASES = [
    {"id": 1, "tag_name": "v2.0", "name": "v2.0", "prerelease": False, "draft": False,
     "published_at": "2026-05-10T00:00:00Z", "body": "stable two", "html_url": "https://x/2", "author": {"login": "a"}},
    {"id": 2, "tag_name": "v2.0-beta.1", "name": "v2.0-beta.1", "prerelease": True, "draft": False,
     "published_at": "2026-05-09T00:00:00Z", "body": "beta", "html_url": "https://x/2b", "author": {"login": "a"}},
    {"id": 3, "tag_name": "v1.9", "name": "v1.9", "prerelease": False, "draft": True,
     "published_at": "2026-05-05T00:00:00Z", "body": "draft", "html_url": "https://x/19", "author": {"login": "a"}},
    {"id": 4, "tag_name": "v1.0", "name": "v1.0", "prerelease": False, "draft": False,
     "published_at": "2026-05-01T00:00:00Z", "body": "stable one", "html_url": "https://x/1", "author": {"login": "a"}},
]


def _make_fetcher():
    fetcher = GenericGitHubReleasesFetcher()
    captured = {}

    async def fake_safe_get(client, url, params=None, headers=None):
        captured["params"] = params or {}
        return DummyJsonResponse(RELEASES)

    fetcher._safe_get = fake_safe_get
    return fetcher, captured


def test_github_releases_excludes_prereleases_and_drafts_when_off():
    fetcher, captured = _make_fetcher()

    async def collect():
        return [item async for item in fetcher._run(None, owner="acme", repo="tool", include_prereleases=False, limit=10)]

    items = asyncio.run(collect())

    # 预发布(id2)与草稿(id3)都被排除，只保留正式 release。
    assert [item.tag_name for item in items] == ["v2.0", "v1.0"]
    # 排除预发布时按 100 取页作为余量（正式版可能稀疏分布在大量 beta 之间）。
    assert captured["params"]["per_page"] == 100


def test_github_releases_includes_prereleases_when_on():
    fetcher, captured = _make_fetcher()

    async def collect():
        return [item async for item in fetcher._run(None, owner="acme", repo="tool", include_prereleases=True, limit=10)]

    items = asyncio.run(collect())

    # 打开预发布：草稿仍被排除，但 beta 保留。
    assert [item.tag_name for item in items] == ["v2.0", "v2.0-beta.1", "v1.0"]
    # 包含预发布时按 limit 取页（保持原有行为）。
    assert captured["params"]["per_page"] == 10


def test_openclaw_defaults_to_stable_only():
    # OpenClaw 每天发多个 beta，默认应跳过预发布。
    assert OpenClawGitHubReleasesFetcher.default_include_prereleases is False


def test_opencode_points_at_active_repo():
    # 原 opencode-ai/opencode 已停更，应指向活跃的 anomalyco/opencode。
    assert OpenCodeGitHubReleasesFetcher.owner == "anomalyco"
    assert OpenCodeGitHubReleasesFetcher.repo == "opencode"
    assert "anomalyco/opencode" in OpenCodeGitHubReleasesFetcher.source_url
