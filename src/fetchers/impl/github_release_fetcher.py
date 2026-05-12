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
            self.logger.error("GitHub owner/repo 不能为空，放弃抓取。")
            return

        self.source_id = runtime_source_id
        url = f"https://api.github.com/repos/{owner}/{repo}/releases"
        response = await self._safe_get(
            client,
            url,
            params={"per_page": limit},
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if not response:
            return

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


class DifyGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_dify_releases"
    name = "Dify GitHub Releases"
    description = "通过 GitHub API 抓取 Dify Release 元数据。"
    icon = "🧩"
    owner = "langgenius"
    repo = "dify"


class VllmGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_vllm_releases"
    name = "vLLM GitHub Releases"
    description = "通过 GitHub API 抓取 vLLM Release 元数据。"
    icon = "⚡"
    owner = "vllm-project"
    repo = "vllm"


class OllamaGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_ollama_releases"
    name = "Ollama GitHub Releases"
    description = "通过 GitHub API 抓取 Ollama Release 元数据。"
    icon = "🦙"
    owner = "ollama"
    repo = "ollama"


class LangChainGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_langchain_releases"
    name = "LangChain GitHub Releases"
    description = "通过 GitHub API 抓取 LangChain Release 元数据。"
    icon = "🦜"
    owner = "langchain-ai"
    repo = "langchain"


class TransformersGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_transformers_releases"
    name = "Transformers GitHub Releases"
    description = "通过 GitHub API 抓取 Hugging Face Transformers Release 元数据。"
    icon = "🤗"
    owner = "huggingface"
    repo = "transformers"


class PytorchGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_pytorch_releases"
    name = "PyTorch GitHub Releases"
    description = "通过 GitHub API 抓取 PyTorch Release 元数据。"
    icon = "🔥"
    owner = "pytorch"
    repo = "pytorch"


class LlamaCppGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_llama_cpp_releases"
    name = "llama.cpp GitHub Releases"
    description = "通过 GitHub API 抓取 llama.cpp Release 元数据。"
    icon = "🧱"
    owner = "ggml-org"
    repo = "llama.cpp"


class LiteLlmGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_litellm_releases"
    name = "LiteLLM GitHub Releases"
    description = "通过 GitHub API 抓取 LiteLLM Release 元数据。"
    icon = "💡"
    owner = "BerriAI"
    repo = "litellm"


class OpenWebUiGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_open_webui_releases"
    name = "Open WebUI GitHub Releases"
    description = "通过 GitHub API 抓取 Open WebUI Release 元数据。"
    icon = "🖥️"
    owner = "open-webui"
    repo = "open-webui"


class ComfyUiGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_comfyui_releases"
    name = "ComfyUI GitHub Releases"
    description = "通过 GitHub API 抓取 ComfyUI Release 元数据。"
    icon = "🎛️"
    owner = "comfyanonymous"
    repo = "ComfyUI"


class OpenAiAgentsPythonGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_openai_agents_python_releases"
    name = "OpenAI Agents SDK Releases"
    description = "通过 GitHub API 抓取 OpenAI Agents Python SDK Release 元数据。"
    icon = "🧠"
    owner = "openai"
    repo = "openai-agents-python"


class ClaudeCodeGitHubReleasesFetcher(PresetGitHubReleasesFetcher):
    source_id = "github_claude_code_releases"
    name = "Claude Code GitHub Releases"
    description = "通过 GitHub API 抓取 Claude Code Release 元数据。"
    icon = "🟧"
    owner = "anthropics"
    repo = "claude-code"
