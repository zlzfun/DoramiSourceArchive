import hashlib
import os
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List

import httpx

from fetchers.base import BaseFetcher
from fetchers.impl.article_extractor import DETAIL_HARD_CAP
from models.content import BaseContent, GitHubRepositoryContent, HuggingFaceModelContent


class GenericGitHubRepositoriesFetcher(BaseFetcher):
    """通用 GitHub 组织新仓库抓取器。"""
    is_template = True  # 通用模板节点:后端保留,前端目录不显现

    source_id = "generic_github_repositories"
    content_type = "github_repository"
    category = "advanced"

    name = "通用 GitHub 新仓库"
    description = "通过 GitHub API 抓取指定组织或用户的最新公开仓库。"
    icon = "🐙"

    default_limit = 10
    default_fetch_readme = True
    # README 补进正文,对齐「下游要全文」原则(仅病态页兜底,与正文同一硬上限)
    default_readme_max_chars = DETAIL_HARD_CAP

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "owner", "label": "Owner / Org", "type": "text", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
            {"field": "include_forks", "label": "包含 fork 仓库", "type": "boolean", "default": False},
            {"field": "include_archived", "label": "包含归档仓库", "type": "boolean", "default": False},
            {"field": "fetch_readme", "label": "无描述时补充 README", "type": "boolean", "default": cls.default_fetch_readme},
            {"field": "readme_max_chars", "label": "README 摘要最大字符", "type": "number", "default": cls.default_readme_max_chars},
        ]

    def _entry_limit(self, raw_limit: Any, default: int = 10) -> int:
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

    def _positive_int_param(self, raw_value: Any, default: int) -> int:
        if raw_value in (None, ""):
            return default
        try:
            return max(int(raw_value), 0)
        except (TypeError, ValueError):
            self.logger.warning(f"参数无效，使用默认值: {raw_value}")
            return default

    def _repo_id(self, runtime_source_id: str, repo: Dict[str, Any]) -> str:
        stable_value = repo.get("id") or repo.get("node_id") or repo.get("full_name")
        digest = hashlib.sha1(str(stable_value).encode("utf-8")).hexdigest()[:16]
        return f"{runtime_source_id}_{digest}"

    def _github_headers(self, accept: str = "application/vnd.github+json") -> Dict[str, str]:
        """GitHub API 请求头；存在 GITHUB_TOKEN/GH_TOKEN 时附带鉴权(限额 60→5000/hr)。"""
        headers = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers

    async def _fetch_repositories(self, client: httpx.AsyncClient, owner: str, limit: int) -> List[Dict[str, Any]]:
        headers = self._github_headers()
        params = {"sort": "created", "direction": "desc", "per_page": limit}
        for path in [f"https://api.github.com/orgs/{owner}/repos", f"https://api.github.com/users/{owner}/repos"]:
            response = await self._safe_get(client, path, params=params, headers=headers)
            if response and response.status_code < 400:
                data = response.json()
                return data if isinstance(data, list) else []
        raise RuntimeError(f"GitHub 仓库请求失败: {owner}")

    _README_SKIP_LINE_RE = re.compile(
        r"^\s*(?:!\[[^\]]*\]\([^)]*\)\s*|\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)\s*|<[^>]+>\s*|[-=*_]{3,}\s*|\|[-:\s|]+\|\s*)+$"
    )
    _README_BADGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img[^>]*>|<a[^>]*>|</a>", re.IGNORECASE)
    _README_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

    def _clean_readme(self, raw: str, max_chars: int) -> str:
        """把 README markdown 清洗成可读的纯文本摘要：去 HTML 注释/徽章/图片/裸标签/
        分隔线与表格分隔行，剥掉行首 markdown 标记，合并空行，按行边界截断。"""
        if not raw:
            return ""
        text = self._README_HTML_COMMENT_RE.sub("", raw)
        lines: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                if lines and lines[-1] != "":
                    lines.append("")
                continue
            if self._README_SKIP_LINE_RE.match(stripped):
                continue
            cleaned = self._README_BADGE_RE.sub("", stripped)
            cleaned = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cleaned)  # 链接→纯文本
            cleaned = re.sub(r"\*\*|`", "", cleaned)             # 加粗/行内代码标记
            cleaned = re.sub(r"^#{1,6}\s*", "", cleaned)        # 标题井号
            cleaned = re.sub(r"^>\s*", "", cleaned)              # 引用
            cleaned = re.sub(r"^[-*+]\s+", "• ", cleaned)        # 无序列表
            if "|" in cleaned:                                   # 表格行 → " · " 分隔
                cells = [cell.strip() for cell in cleaned.strip("|").split("|")]
                cleaned = " · ".join(cell for cell in cells if cell)
            cleaned = cleaned.strip()
            if cleaned:
                lines.append(cleaned)
        excerpt = "\n".join(lines).strip()
        excerpt = re.sub(r"\n{3,}", "\n\n", excerpt)
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n…"
        return excerpt

    async def _fetch_readme(self, client: httpx.AsyncClient, owner: str, repo_name: str, max_chars: int) -> str:
        """拉取仓库 README 原文并清洗为摘要；无 README 或失败时安静返回空串。

        故意不走 ``_safe_get``：缺 README 是合法的 404，不该触发重试与错误日志。
        """
        if not repo_name:
            return ""
        url = f"https://api.github.com/repos/{owner}/{repo_name}/readme"
        try:
            response = await client.get(url, headers=self._github_headers("application/vnd.github.raw+json"))
        except httpx.HTTPError as e:
            self.logger.info(f"ℹ️ README 拉取失败，跳过 [{owner}/{repo_name}]: {e}")
            return ""
        if response.status_code != 200 or not response.text:
            return ""
        return self._clean_readme(response.text, max_chars)

    def _repo_content(self, repo: Dict[str, Any], runtime_source_id: str, owner: str, readme_excerpt: str = "") -> GitHubRepositoryContent:
        full_name = repo.get("full_name", "") or f"{owner}/{repo.get('name', '')}".strip("/")
        description = repo.get("description", "") or ""
        license_info = repo.get("license") or {}
        created_at = repo.get("created_at") or datetime.now(timezone.utc).isoformat()
        lines = [
            f"GitHub repository: {full_name}",
            f"Description: {description or 'No repository description provided.'}",
            f"Language: {repo.get('language') or 'Unknown'}",
            f"Stars: {int(repo.get('stargazers_count') or 0)}",
            f"URL: {repo.get('html_url', '')}",
        ]
        if readme_excerpt:
            lines.append("Readme excerpt:")
            lines.append(readme_excerpt)
        content = "\n".join(lines).strip()
        return GitHubRepositoryContent(
            id=self._repo_id(runtime_source_id, repo),
            title=full_name,
            source_url=repo.get("html_url", ""),
            publish_date=created_at,
            content=content,
            has_content=bool(content),
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
                "readme_chars": len(readme_excerpt),
            },
        )

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        owner = str(kwargs.get("owner", "")).strip()
        runtime_source_id = str(kwargs.get("source_id", "")).strip() or self.source_id
        limit = self._entry_limit(kwargs.get("limit"), self.default_limit)
        include_forks = self._bool_param(kwargs.get("include_forks"), False)
        include_archived = self._bool_param(kwargs.get("include_archived"), False)
        fetch_readme = self._bool_param(kwargs.get("fetch_readme"), self.default_fetch_readme)
        readme_max_chars = self._positive_int_param(kwargs.get("readme_max_chars"), self.default_readme_max_chars)
        if not owner:
            raise ValueError("GitHub owner 不能为空")

        self.source_id = runtime_source_id
        repos = [
            repo for repo in await self._fetch_repositories(client, owner, limit)
            if not (repo.get("fork") and not include_forks)
            and not (repo.get("archived") and not include_archived)
        ]

        # 去重预检：已入库且有正文的仓库无需重复拉 README（仅描述为空者才会触发拉取）。
        existing_flags = await self._lookup_existing_content_flags(
            self._repo_id(runtime_source_id, repo) for repo in repos
        )

        emitted = 0
        for repo in repos:
            readme_excerpt = ""
            description = (repo.get("description") or "").strip()
            already_stored = existing_flags.get(self._repo_id(runtime_source_id, repo), False)
            if fetch_readme and not description and not already_stored:
                readme_excerpt = await self._fetch_readme(client, owner, repo.get("name", ""), readme_max_chars)
            yield self._repo_content(repo, runtime_source_id, owner, readme_excerpt)
            emitted += 1
            if emitted >= limit:
                break


class PresetGitHubRepositoriesFetcher(GenericGitHubRepositoriesFetcher):
    is_template = False  # preset 固化节点:重置 Generic 基类的模板标志
    source_id = "unknown_source"
    owner = ""
    category = "primary"
    default_limit = 10

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        # 参数固化波:抓取偏好属于节点本身(fork/归档恒排除、README 恒补充取全文),
        # 不作用户参数;调整 = 改代码。
        return [{"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit}]

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
    source_owner = "deepseek-ai"
    source_brand = "deepseek"
    source_scope = "open_model_family"
    source_channel = "github_repository_activity"
    source_url = "https://github.com/deepseek-ai"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "developer_tool", "research_paper"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"


class GenericHuggingFaceModelsFetcher(BaseFetcher):
    """通用 Hugging Face 作者/组织新模型抓取器。"""
    is_template = True  # 通用模板节点:后端保留,前端目录不显现

    source_id = "generic_huggingface_models"
    content_type = "hf_model"
    category = "advanced"

    name = "通用 Hugging Face 新模型"
    description = "通过 Hugging Face API 抓取指定作者或组织的最新模型。"
    icon = "🤗"
    default_limit = 10

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "author", "label": "Author / Org", "type": "text", "default": ""},
            {"field": "source_id", "label": "数据源 ID", "type": "text", "default": ""},
            {"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit},
        ]

    def _entry_limit(self, raw_limit: Any, default: int = 10) -> int:
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
        content = "\n".join([
            f"Hugging Face model: {model_id or 'Unknown model'}",
            f"Summary: {summary or 'No model-card summary provided.'}",
            f"Pipeline tag: {model.get('pipeline_tag') or 'Unknown'}",
            f"Library: {model.get('library_name') or 'Unknown'}",
            f"Tags: {', '.join(str(tag) for tag in tags[:12])}",
            f"URL: {'https://huggingface.co/' + model_id if model_id else 'https://huggingface.co/models'}",
        ]).strip()
        return HuggingFaceModelContent(
            id=self._model_id(runtime_source_id, model),
            title=model_id or "未命名 Hugging Face 模型",
            source_url=f"https://huggingface.co/{model_id}" if model_id else "https://huggingface.co/models",
            publish_date=self._model_datetime(model),
            content=content,
            has_content=bool(content),
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
        limit = self._entry_limit(kwargs.get("limit"), self.default_limit)
        if not author:
            raise ValueError("Hugging Face author 不能为空")

        self.source_id = runtime_source_id
        response = await self._safe_get(
            client,
            "https://huggingface.co/api/models",
            params={"author": author, "sort": "createdAt", "direction": -1, "limit": limit, "full": 1},
        )
        if not response:
            raise RuntimeError(f"Hugging Face 模型请求失败: {author}")

        models = response.json()
        if not isinstance(models, list):
            raise RuntimeError(f"Hugging Face API 返回了非列表结构: {author}")

        for model in models[:limit]:
            yield self._model_content(model, runtime_source_id, author)


class PresetHuggingFaceModelsFetcher(GenericHuggingFaceModelsFetcher):
    is_template = False  # preset 固化节点:重置 Generic 基类的模板标志
    source_id = "unknown_source"
    author = ""
    category = "primary"
    default_limit = 10

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [{"field": "limit", "label": "单次获取上限", "type": "number", "default": cls.default_limit}]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        params = {**kwargs, "author": self.author, "source_id": self.source_id, "limit": kwargs.get("limit", self.default_limit)}
        async for item in super()._run(client, **params):
            yield item


class DeepSeekHuggingFaceModelsFetcher(PresetHuggingFaceModelsFetcher):
    source_id = "hf_deepseek_models"
    name = "DeepSeek Hugging Face 新模型"
    description = "跟踪 DeepSeek 在 Hugging Face 上发布的新模型。"
    icon = "🤗"
    author = "deepseek-ai"
    source_owner = "deepseek-ai"
    source_brand = "deepseek"
    source_scope = "open_model_family"
    source_channel = "model_repository"
    source_url = "https://huggingface.co/deepseek-ai"
    provenance_tier = "tier0_primary"
    content_tags = ["model_release", "research_paper"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "stable_public"
