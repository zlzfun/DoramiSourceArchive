import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List

import httpx

from fetchers.base import BaseFetcher
from models.content import BaseContent, GitHubReleaseContent


class GenericGitHubReleasesFetcher(BaseFetcher):
    """
    通用 GitHub Releases API 抓取器。

    与 GitHub Atom feed 相比，API 形态能保留 tag、作者、预发布标记、资产列表等结构化字段，
    更适合归档产品/开发者生态的版本更新。
    """
    source_id = "generic_github_releases"
    content_type = "github_release"
    category = "advanced"

    name = "通用 GitHub Releases"
    description = "通过 GitHub API 抓取指定仓库的 Release 元数据，适合 AI 工具、框架与产品更新。"
    icon = "🐙"

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "owner", "label": "Owner", "type": "text", "default": ""},
            {"field": "repo", "label": "Repo", "type": "text", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 10},
            {"field": "include_prereleases", "label": "包含预发布", "type": "boolean", "default": True},
        ]

    def _entry_limit(self, raw_limit: Any, default: int = 10) -> int:
        if raw_limit in (None, ""):
            return default
        try:
            return min(max(int(raw_limit), 0), 100)
        except (TypeError, ValueError):
            self.logger.warning(f"GitHub Release 条数参数无效，使用默认值: {raw_limit}")
            return default

    def _bool_param(self, raw_value: Any, default: bool = True) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value in (None, ""):
            return default
        return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _release_id(self, runtime_source_id: str, release: Dict[str, Any]) -> str:
        stable_value = release.get("id") or release.get("node_id") or release.get("html_url") or release.get("tag_name")
        digest = hashlib.sha1(str(stable_value).encode("utf-8")).hexdigest()[:16]
        return f"{runtime_source_id}_{digest}"

    def _release_datetime(self, release: Dict[str, Any]) -> str:
        return (
            release.get("published_at")
            or release.get("created_at")
            or datetime.now(timezone.utc).isoformat()
        )

    def _release_assets(self, release: Dict[str, Any]) -> List[Dict[str, Any]]:
        assets = []
        for asset in release.get("assets", []) or []:
            assets.append({
                "name": asset.get("name", ""),
                "label": asset.get("label", ""),
                "content_type": asset.get("content_type", ""),
                "size": asset.get("size", 0),
                "download_count": asset.get("download_count", 0),
                "browser_download_url": asset.get("browser_download_url", ""),
                "created_at": asset.get("created_at", ""),
                "updated_at": asset.get("updated_at", ""),
            })
        return assets

    def _release_content(
            self,
            release: Dict[str, Any],
            runtime_source_id: str,
            owner: str,
            repo: str,
    ) -> GitHubReleaseContent:
        repository = f"{owner}/{repo}"
        tag_name = release.get("tag_name", "")
        release_name = release.get("name", "") or tag_name
        title = f"{repository} {release_name}".strip()
        author = release.get("author") or {}

        return GitHubReleaseContent(
            id=self._release_id(runtime_source_id, release),
            title=title or "未命名 GitHub Release",
            source_url=release.get("html_url", ""),
            publish_date=self._release_datetime(release),
            content=release.get("body", "") or "",
            has_content=bool(release.get("body", "")),
            repository=repository,
            owner=owner,
            repo=repo,
            tag_name=tag_name,
            release_name=release_name,
            author_login=author.get("login", ""),
            target_commitish=release.get("target_commitish", ""),
            draft=bool(release.get("draft", False)),
            prerelease=bool(release.get("prerelease", False)),
            assets=self._release_assets(release),
            tarball_url=release.get("tarball_url", ""),
            zipball_url=release.get("zipball_url", ""),
            raw_data={
                "id": release.get("id"),
                "node_id": release.get("node_id", ""),
                "url": release.get("url", ""),
                "html_url": release.get("html_url", ""),
                "created_at": release.get("created_at", ""),
                "published_at": release.get("published_at", ""),
            },
        )

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        owner = str(kwargs.get("owner", "")).strip()
        repo = str(kwargs.get("repo", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        limit = self._entry_limit(kwargs.get("limit"), 10)
        include_prereleases = self._bool_param(kwargs.get("include_prereleases"), True)

        if not owner or not repo:
            raise ValueError("GitHub owner/repo 不能为空")

        self.source_id = runtime_source_id
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        # 排除预发布时，正式 release 可能稀疏地分布在大量 beta 之间，按 limit 取页会几乎全被
        # 过滤掉；此时多取一页（GitHub 上限 100）作为余量，确保过滤后仍能凑够 limit 条正式版。
        per_page = limit if include_prereleases else 100
        response = await self._safe_get(
            client,
            url,
            params={"per_page": per_page},
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if not response:
            raise RuntimeError(f"GitHub Releases 请求失败: {owner}/{repo}")

        releases = response.json()
        if not isinstance(releases, list):
            self.logger.warning(f"GitHub Releases API 返回了非列表结构: {owner}/{repo}")
            return

        emitted_count = 0
        for release in releases:
            if release.get("draft"):
                continue
            if release.get("prerelease") and not include_prereleases:
                continue

            emitted_count += 1
            yield self._release_content(release, runtime_source_id, owner, repo)

            if emitted_count >= limit:
                break


class PresetGitHubReleasesFetcher(GenericGitHubReleasesFetcher):
    """预设 GitHub Releases API 抓取器基类。"""

    source_id = "unknown_source"
    owner = ""
    repo = ""
    category = "product_update"
    default_limit = 10
    default_include_prereleases = True

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {
                "field": "include_prereleases",
                "label": "包含预发布",
                "type": "boolean",
                "default": cls.default_include_prereleases,
            },
        ]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        params = {
            **kwargs,
            "owner": self.owner,
            "repo": self.repo,
            "source_id": self.source_id,
            "limit": kwargs.get("limit", self.default_limit),
            "include_prereleases": kwargs.get("include_prereleases", self.default_include_prereleases),
        }
        async for item in super()._run(client, **params):
            yield item


class OpenCodeGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_opencode_releases"
    name = "OpenCode GitHub Releases"
    description = "通过 GitHub API 抓取 OpenCode Release 元数据。"
    icon = "⌨️"
    # 原 opencode-ai/opencode 自 2025-06(v0.0.55)起停更，项目已迁移；活跃仓库现为
    # anomalyco/opencode（前 sst/opencode，持续发布 v1.x）。指向活跃仓库以恢复有效信号。
    owner = "anomalyco"
    repo = "opencode"
    source_owner = "opencode"
    source_brand = "opencode"
    source_scope = "developer_tool"
    source_channel = "github_release"
    source_url = "https://github.com/anomalyco/opencode/releases"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update"]
    signal_strength = "medium_signal"
    noise_risk = "high_noise"
    fetch_reliability = "stable_public"


class OpenClawGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_openclaw_releases"
    name = "OpenClaw GitHub Releases"
    description = "通过 GitHub API 抓取 OpenClaw 正式 Release 元数据（默认跳过 beta 预发布）。"
    icon = "🧰"
    owner = "openclaw"
    repo = "openclaw"
    source_owner = "openclaw"
    source_brand = "openclaw"
    source_scope = "developer_tool"
    source_channel = "github_release"
    source_url = "https://github.com/openclaw/openclaw/releases"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update", "api_platform"]
    signal_strength = "medium_signal"
    noise_risk = "high_noise"
    fetch_reliability = "stable_public"
    # OpenClaw 每天发多个 -beta 预发布（最近 12 个 release 有 11 个是 beta、同日多条重复），
    # 噪声极高。默认只保留正式 release；需要 beta 时可在参数里打开 include_prereleases。
    default_include_prereleases = False


class HermesAgentGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_hermes_agent_releases"
    name = "Hermes Agent GitHub Releases"
    description = "通过 GitHub API 抓取 NousResearch Hermes Agent Release 元数据。"
    icon = "🪽"
    owner = "NousResearch"
    repo = "hermes-agent"
    source_owner = "nousresearch"
    source_brand = "hermes_agent"
    source_scope = "developer_tool"
    source_channel = "github_release"
    source_url = "https://github.com/NousResearch/hermes-agent/releases"
    provenance_tier = "tier0_primary"
    content_tags = ["developer_tool", "product_update", "api_platform"]
    signal_strength = "medium_signal"
    noise_risk = "high_noise"
    fetch_reliability = "stable_public"
