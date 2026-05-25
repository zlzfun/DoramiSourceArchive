import hashlib
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List

import httpx

from fetchers.base import BaseFetcher
from models.content import BaseContent, GitHubRepositoryContent, HuggingFaceModelContent


class GenericGitHubRepositoriesFetcher(BaseFetcher):
    """通用 GitHub 组织新仓库抓取器。"""

    source_id = "generic_github_repositories"
    content_type = "github_repository"
    category = "advanced"

    name = "通用 GitHub 新仓库"
    description = "通过 GitHub API 抓取指定组织或用户的最新公开仓库。"
    icon = "🐙"

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "owner", "label": "Owner / Org", "type": "text", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 20},
            {"field": "include_forks", "label": "包含 fork 仓库", "type": "boolean", "default": False},
            {"field": "include_archived", "label": "包含归档仓库", "type": "boolean", "default": False},
        ]

    def _entry_limit(self, raw_limit: Any, default: int = 20) -> int:
        if raw_limit in (None, ""):
            return default
        try:
            return min(max(int(raw_limit), 0), 100)
        except (TypeError, ValueError):
            self.logger.warning(f"GitHub 仓库条数参数无效，使用默认值: {raw_limit}")
            return default

    def _bool_param(self, raw_value: Any, default: bool = False) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if raw_value in (None, ""):
            return default
        return str(raw_value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def _repo_id(self, runtime_source_id: str, repo: Dict[str, Any]) -> str:
        stable_value = repo.get("id") or repo.get("node_id") or repo.get("full_name")
        digest = hashlib.sha1(str(stable_value).encode("utf-8")).hexdigest()[:16]
        return f"{runtime_source_id}_{digest}"

    async def _fetch_repositories(self, client: httpx.AsyncClient, owner: str, limit: int) -> List[Dict[str, Any]]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        params = {"sort": "created", "direction": "desc", "per_page": limit}
        for path in [f"https://api.github.com/orgs/{owner}/repos", f"https://api.github.com/users/{owner}/repos"]:
            response = await self._safe_get(client, path, params=params, headers=headers)
            if response and response.status_code < 400:
                data = response.json()
                return data if isinstance(data, list) else []
        return []

    def _repo_content(self, repo: Dict[str, Any], runtime_source_id: str, owner: str) -> GitHubRepositoryContent:
        full_name = repo.get("full_name", "") or f"{owner}/{repo.get('name', '')}".strip("/")
        description = repo.get("description", "") or ""
        license_info = repo.get("license") or {}
        created_at = repo.get("created_at") or datetime.now(timezone.utc).isoformat()
        return GitHubRepositoryContent(
            id=self._repo_id(runtime_source_id, repo),
            title=full_name,
            source_url=repo.get("html_url", ""),
            publish_date=created_at,
            content=description,
            has_content=bool(description),
            repository=full_name,
            owner=owner,
            repo=repo.get("name", ""),
            description=description,
            language=repo.get("language") or "",
            default_branch=repo.get("default_branch") or "",
            stars=int(repo.get("stargazers_count") or 0),
            forks=int(repo.get("forks_count") or 0),
            open_issues=int(repo.get("open_issues_count") or 0),
            archived=bool(repo.get("archived")),
            fork=bool(repo.get("fork")),
            license_name=license_info.get("name", "") if isinstance(license_info, dict) else "",
            pushed_at=repo.get("pushed_at") or "",
            updated_at=repo.get("updated_at") or "",
            raw_data={
                "id": repo.get("id"),
                "node_id": repo.get("node_id", ""),
                "url": repo.get("url", ""),
                "created_at": repo.get("created_at", ""),
                "updated_at": repo.get("updated_at", ""),
                "pushed_at": repo.get("pushed_at", ""),
            },
        )

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        owner = str(kwargs.get("owner", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        limit = self._entry_limit(kwargs.get("limit"), 20)
        include_forks = self._bool_param(kwargs.get("include_forks"), False)
        include_archived = self._bool_param(kwargs.get("include_archived"), False)
        if not owner:
            self.logger.error("GitHub owner 不能为空，放弃抓取。")
            return

        self.source_id = runtime_source_id
        emitted = 0
        for repo in await self._fetch_repositories(client, owner, limit):
            if repo.get("fork") and not include_forks:
                continue
            if repo.get("archived") and not include_archived:
                continue
            yield self._repo_content(repo, runtime_source_id, owner)
            emitted += 1
            if emitted >= limit:
                break


class PresetGitHubRepositoriesFetcher(GenericGitHubRepositoriesFetcher):
    source_id = "unknown_source"
    owner = ""
    category = "primary"
    default_limit = 20

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "include_forks", "label": "包含 fork 仓库", "type": "boolean", "default": False},
            {"field": "include_archived", "label": "包含归档仓库", "type": "boolean", "default": False},
        ]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        params = {**kwargs, "owner": self.owner, "source_id": self.source_id, "limit": kwargs.get("limit", self.default_limit)}
        async for item in super()._run(client, **params):
            yield item


class DeepSeekGitHubRepositoriesFetcher(PresetGitHubRepositoriesFetcher):
    source_id = "github_deepseek_repositories"
    name = "DeepSeek GitHub 新仓库"
    description = "跟踪 deepseek-ai GitHub 组织下的新公开仓库。"
    icon = "🧠"
    owner = "deepseek-ai"


class InclusionAiGitHubRepositoriesFetcher(PresetGitHubRepositoriesFetcher):
    source_id = "github_inclusion_ai_repositories"
    name = "inclusionAI GitHub 新仓库"
    description = "跟踪 inclusionAI GitHub 组织下的新公开仓库。"
    icon = "🐜"
    owner = "inclusionAI"


class TencentHunyuanGitHubRepositoriesFetcher(PresetGitHubRepositoriesFetcher):
    source_id = "github_tencent_hunyuan_repositories"
    name = "腾讯混元 GitHub 新仓库"
    description = "跟踪 Tencent-Hunyuan GitHub 组织下的新公开仓库。"
    icon = "💧"
    owner = "Tencent-Hunyuan"


class GenericHuggingFaceModelsFetcher(BaseFetcher):
    """通用 Hugging Face 作者/组织新模型抓取器。"""

    source_id = "generic_huggingface_models"
    content_type = "hf_model"
    category = "advanced"

    name = "通用 Hugging Face 新模型"
    description = "通过 Hugging Face API 抓取指定作者或组织的最新模型。"
    icon = "🤗"

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "author", "label": "Author / Org", "type": "text", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": 20},
        ]

    def _entry_limit(self, raw_limit: Any, default: int = 20) -> int:
        if raw_limit in (None, ""):
            return default
        try:
            return min(max(int(raw_limit), 0), 100)
        except (TypeError, ValueError):
            self.logger.warning(f"Hugging Face 模型条数参数无效，使用默认值: {raw_limit}")
            return default

    def _model_id(self, runtime_source_id: str, model: Dict[str, Any]) -> str:
        stable_value = model.get("id") or model.get("modelId") or repr(model)
        digest = hashlib.sha1(str(stable_value).encode("utf-8")).hexdigest()[:16]
        return f"{runtime_source_id}_{digest}"

    def _model_datetime(self, model: Dict[str, Any]) -> str:
        return model.get("createdAt") or model.get("lastModified") or datetime.now(timezone.utc).isoformat()

    def _model_content(self, model: Dict[str, Any], runtime_source_id: str, author: str) -> HuggingFaceModelContent:
        model_id = model.get("modelId") or model.get("id") or ""
        tags = model.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        card_data = model.get("cardData") if isinstance(model.get("cardData"), dict) else {}
        summary = card_data.get("summary") or model.get("description") or ""
        return HuggingFaceModelContent(
            id=self._model_id(runtime_source_id, model),
            title=model_id or "未命名 Hugging Face 模型",
            source_url=f"https://huggingface.co/{model_id}" if model_id else "https://huggingface.co/models",
            publish_date=self._model_datetime(model),
            content=summary,
            has_content=bool(summary),
            model_id=model_id,
            author=author,
            pipeline_tag=model.get("pipeline_tag") or "",
            library_name=model.get("library_name") or "",
            tags=[str(tag) for tag in tags],
            downloads=int(model.get("downloads") or 0),
            likes=int(model.get("likes") or 0),
            last_modified=model.get("lastModified") or "",
            gated=str(model.get("gated") or ""),
            private=bool(model.get("private")),
            raw_data={
                "id": model.get("id", ""),
                "modelId": model.get("modelId", ""),
                "createdAt": model.get("createdAt", ""),
                "lastModified": model.get("lastModified", ""),
            },
        )

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        author = str(kwargs.get("author", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        limit = self._entry_limit(kwargs.get("limit"), 20)
        if not author:
            self.logger.error("Hugging Face author 不能为空，放弃抓取。")
            return

        self.source_id = runtime_source_id
        response = await self._safe_get(
            client,
            "https://huggingface.co/api/models",
            params={"author": author, "sort": "createdAt", "direction": -1, "limit": limit, "full": 1},
        )
        if not response:
            return

        models = response.json()
        if not isinstance(models, list):
            self.logger.warning(f"Hugging Face API 返回了非列表结构: {author}")
            return

        for model in models[:limit]:
            yield self._model_content(model, runtime_source_id, author)


class PresetHuggingFaceModelsFetcher(GenericHuggingFaceModelsFetcher):
    source_id = "unknown_source"
    author = ""
    category = "primary"
    default_limit = 20

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [{"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit}]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        params = {**kwargs, "author": self.author, "source_id": self.source_id, "limit": kwargs.get("limit", self.default_limit)}
        async for item in super()._run(client, **params):
            yield item


class InclusionAiHuggingFaceModelsFetcher(PresetHuggingFaceModelsFetcher):
    source_id = "hf_inclusion_ai_models"
    name = "inclusionAI Hugging Face 新模型"
    description = "跟踪 inclusionAI 在 Hugging Face 上发布的新模型。"
    icon = "🐜"
    author = "inclusionAI"


class LongCatHuggingFaceModelsFetcher(PresetHuggingFaceModelsFetcher):
    source_id = "hf_longcat_models"
    name = "美团 LongCat Hugging Face 新模型"
    description = "跟踪美团 LongCat 在 Hugging Face 上发布的新模型。"
    icon = "🍱"
    author = "meituan-longcat"
