"""X API v2 用户时间线抓取器与首批账号预设。"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Any, AsyncGenerator, Dict, List, Optional
from urllib.parse import quote

import httpx
from sqlmodel import Session

from config import XApiConfig, settings
from fetchers.base import BaseFetcher
from models.content import BaseContent, SocialPostContent
from models.db import SourceStateRecord
from services.x_api_config import (
    read_user_cache,
    resolve_x_api_config,
    write_user_cache,
)
from services.x_api_quota import USER_READ_MICROS, XApiQuotaGuard
from services.x_social_normalizer import (
    normalize_x_post_extensions,
    x_includes,
    x_post_text,
)


_TWEET_FIELDS = ",".join(
    [
        "article",
        "attachments",
        "author_id",
        "conversation_id",
        "created_at",
        "edit_history_tweet_ids",
        "entities",
        "lang",
        "note_tweet",
        "possibly_sensitive",
        "public_metrics",
        "referenced_tweets",
    ]
)
_EXPANSIONS = ",".join(
    [
        "author_id",
        "attachments.media_keys",
        "referenced_tweets.id",
        "referenced_tweets.id.author_id",
    ]
)
_USER_FIELDS = "id,name,username,profile_image_url,protected,verified"
_MEDIA_FIELDS = ",".join(
    [
        "alt_text",
        "duration_ms",
        "height",
        "media_key",
        "preview_image_url",
        "public_metrics",
        "type",
        "url",
        "variants",
        "width",
    ]
)


class XTimelineFetcher(BaseFetcher):
    """参数驱动的 X user timeline 抓取器。

    通用模板接受 ``handle``/``user_id``；具体账号由下方 preset 固化。数据库
    engine 由 DataPipeline 注入，用于读取 SourceStateRecord.since_id 和持久化
    AppSettingRecord 配额，不在 fetcher 内导入 api.app。
    """

    is_template = True
    source_id = "generic_x_timeline"
    content_type = "social_post"
    content_shape = "social"
    platform = "x"
    category = "advanced"

    name = "通用 X 用户时间线"
    description = "通过 X API v2 抓取指定公开账号的原创、转发与引用动态（排除回复）。"
    icon = "𝕏"
    source_channel = "x_user_timeline"
    source_url = "https://x.com/"
    provenance_tier = "tier0_primary"
    content_tags = ["social_update"]
    signal_strength = "high_signal"
    noise_risk = "medium_noise"
    fetch_reliability = "official_paid_api"

    handle = ""
    user_id = ""

    def __init__(
        self,
        timeout: Optional[int] = None,
        max_retries: int = 3,
        *,
        x_config: Optional[XApiConfig] = None,
        runtime_engine=None,
    ):
        self._uses_runtime_config = x_config is None
        self._timeout_explicit = timeout is not None
        self.x_config = x_config or settings.x_api
        super().__init__(
            timeout=timeout if timeout is not None else self.x_config.timeout_seconds,
            max_retries=max_retries,
        )
        self._runtime_engine = None
        if runtime_engine is not None:
            self.bind_runtime_engine(runtime_engine)

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {"field": "handle", "label": "X Handle", "type": "text", "default": ""},
            {"field": "user_id", "label": "X User ID", "type": "text", "default": ""},
            {
                "field": "limit",
                "label": "单次获取上限",
                "type": "number",
                "default": max(min(settings.x_api.max_results, 100), 5),
            },
        ]

    def bind_runtime_engine(self, engine) -> None:
        """由 DataPipeline 注入现有归档数据库 engine。"""
        self._runtime_engine = engine
        if self._uses_runtime_config:
            with Session(engine) as session:
                self.x_config = resolve_x_api_config(session)
            if not self._timeout_explicit:
                self.timeout = self.x_config.timeout_seconds

    def _require_runtime_engine(self):
        if self._runtime_engine is None:
            raise RuntimeError("X 抓取器缺少持久化运行上下文，必须通过 DataPipeline 执行")
        return self._runtime_engine

    def _quota(self) -> XApiQuotaGuard:
        return XApiQuotaGuard(
            self._require_runtime_engine(),
            monthly_budget_usd=self.x_config.monthly_budget_usd,
        )

    def _request_limit(self, raw_limit: Any) -> int:
        hard_cap = max(min(int(self.x_config.max_results or 25), 100), 5)
        if raw_limit in (None, ""):
            return hard_cap
        try:
            return min(max(int(raw_limit), 5), hard_cap)
        except (TypeError, ValueError):
            self.logger.warning("X 时间线 limit 无效，使用配置上限 %d", hard_cap)
            return hard_cap

    def _since_id(self, runtime_source_id: str) -> str:
        with Session(self._require_runtime_engine()) as session:
            state = session.get(SourceStateRecord, runtime_source_id)
            cursor = str(state.last_cursor_value or "").strip() if state else ""
        if cursor and not cursor.isdigit():
            # 兼容旧流水线把复合 article id 写进 cursor 的历史状态；本次成功后会
            # 由 latest_cursor_value 自动迁正为 X 原始 numeric post_id。
            self.logger.warning("忽略非数字 X since_id 游标，执行一次受限回补: %s", runtime_source_id)
            return ""
        return cursor

    @property
    def _auth_headers(self) -> Dict[str, str]:
        token = self.x_config.bearer_token.strip()
        if not token:
            raise RuntimeError("X API 未配置：请设置 DORAMI_X_BEARER_TOKEN 或 [x_api] bearer_token")
        return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.x_config.base_url.rstrip('/')}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await client.get(url, params=params, headers=self._auth_headers)
                if response.status_code < 400:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise RuntimeError("X API 返回了非对象 JSON")
                    return payload
                if response.status_code not in {429, 500, 502, 503, 504}:
                    raise RuntimeError(f"X API 请求失败（HTTP {response.status_code}）")
                last_error = RuntimeError(f"X API 暂时不可用（HTTP {response.status_code}）")
            except (httpx.HTTPError, ValueError, RuntimeError) as exc:
                last_error = exc
                if isinstance(exc, RuntimeError) and "请求失败" in str(exc):
                    raise

            if attempt < self.max_retries:
                await asyncio.sleep(min(2 ** (attempt - 1), 8))

        raise RuntimeError(f"X API 请求重试耗尽: {last_error}")

    async def _resolve_user(
        self,
        client: httpx.AsyncClient,
        *,
        handle: str,
        user_id: str,
        runtime_source_id: str,
        quota: XApiQuotaGuard,
    ) -> tuple[str, Dict[str, Any]]:
        # 该持久缓存主要服务管理面创建的 handle-only config 源：第一次解析后，
        # 后续抓取不再付费调用 users/by/username。8 个策展 preset 均已固化稳定
        # user_id，本来就直接走下方 user_id 分支，不依赖此缓存来节省解析费用。
        with Session(self._require_runtime_engine()) as session:
            cached = read_user_cache(session, runtime_source_id, handle=handle)
            if cached and (not user_id or str(cached.get("user_id")) == user_id):
                return str(cached["user_id"]), {
                    "id": str(cached["user_id"]),
                    "username": str(cached.get("handle") or handle),
                    "name": str(cached.get("author_name") or cached.get("handle") or handle),
                    "profile_image_url": str(cached.get("author_avatar_url") or ""),
                }
        if user_id:
            user = {"id": user_id, "username": handle}
            with Session(self._require_runtime_engine()) as session:
                write_user_cache(
                    session, runtime_source_id, handle=handle, user_id=user_id, user=user
                )
            return user_id, {**user, "name": handle}
        if not handle:
            raise ValueError("X handle 与 user_id 至少提供一个")
        quota.ensure_available(minimum_cost_micros=USER_READ_MICROS)
        payload = await self._get_json(
            client,
            f"users/by/username/{quote(handle, safe='')}",
            params={"user.fields": _USER_FIELDS},
        )
        quota.record_response(payload, primary_resource="user", source_id=runtime_source_id)
        user = payload.get("data") if isinstance(payload.get("data"), dict) else None
        if not user or not user.get("id"):
            raise RuntimeError(f"X 账号无法解析: @{handle}")
        with Session(self._require_runtime_engine()) as session:
            write_user_cache(
                session,
                runtime_source_id,
                handle=handle,
                user_id=str(user["id"]),
                user=user,
            )
        return str(user["id"]), user

    @staticmethod
    def _title(text: str, post_id: str) -> str:
        first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
        normalized = re.sub(r"\s+", " ", first_line).strip()
        return normalized[:80] or f"X post {post_id}"

    def _content_for_post(
        self,
        post: Dict[str, Any],
        *,
        runtime_source_id: str,
        fallback_handle: str,
        fallback_user: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> SocialPostContent:
        post_id = str(post.get("id") or "").strip()
        if not post_id:
            raise ValueError("X API 帖子缺少 id")

        normalized = normalize_x_post_extensions(
            post,
            payload,
            fallback_handle=fallback_handle,
            fallback_user=fallback_user,
        )
        text = x_post_text(post)
        author_handle = normalized["author_handle"]

        item = SocialPostContent(
            id=f"{runtime_source_id}_{post_id}",
            title=self._title(text, post_id),
            source_url=f"https://x.com/{author_handle}/status/{post_id}",
            publish_date=str(post.get("created_at") or dt.datetime.now(dt.timezone.utc).isoformat()),
            source_id=runtime_source_id,
            content_format="txt",
            content=text,
            has_content=bool(text),
            **normalized,
            raw_data={
                "data": post,
                "includes": payload.get("includes") or {},
            },
        )
        # 不进入 dataclass 序列化，仅供 DataPipeline 将 SourceState cursor 写成
        # numeric X post_id，而 ArticleRecord.id 继续保持 source_id+post_id 幂等键。
        item._cursor_value = post_id
        return item

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        runtime_source_id = str(kwargs.get("source_id") or self.source_id).strip()
        handle = str(kwargs.get("handle") or self.handle).strip().lstrip("@")
        user_id = str(kwargs.get("user_id") or self.user_id).strip()
        quota = self._quota()
        user_id, fallback_user = await self._resolve_user(
            client,
            handle=handle,
            user_id=user_id,
            runtime_source_id=runtime_source_id,
            quota=quota,
        )

        limit = quota.cap_post_results(self._request_limit(kwargs.get("limit")))
        params: Dict[str, Any] = {
            "max_results": limit,
            "exclude": "replies",
            "tweet.fields": _TWEET_FIELDS,
            "expansions": _EXPANSIONS,
            "user.fields": _USER_FIELDS,
            "media.fields": _MEDIA_FIELDS,
        }
        since_id = self._since_id(runtime_source_id)
        if since_id:
            params["since_id"] = since_id

        payload = await self._get_json(client, f"users/{user_id}/tweets", params=params)
        quota.record_response(payload, primary_resource="post", source_id=runtime_source_id)
        timeline_user = next(
            (
                user for user in x_includes(payload, "users")
                if str(user.get("id") or "") == user_id
            ),
            None,
        )
        if timeline_user:
            with Session(self._require_runtime_engine()) as session:
                write_user_cache(
                    session,
                    runtime_source_id,
                    handle=handle,
                    user_id=user_id,
                    user=timeline_user,
                )
        posts = payload.get("data") if isinstance(payload.get("data"), list) else []
        for post in posts:
            if isinstance(post, dict):
                yield self._content_for_post(
                    post,
                    runtime_source_id=runtime_source_id,
                    fallback_handle=handle,
                    fallback_user=fallback_user,
                    payload=payload,
                )


class PresetXTimelineFetcher(XTimelineFetcher):
    """固化账号的 X 时间线 preset 基类。"""

    is_template = False
    source_id = "unknown_source"
    category = "incubating"
    content_type = "social_post"
    content_shape = "social"
    default_limit = 25

    @classmethod
    def get_parameter_schema(cls) -> List[Dict[str, Any]]:
        return [
            {
                "field": "limit",
                "label": "单次获取上限",
                "type": "number",
                "default": cls.default_limit,
            }
        ]

    async def _run(self, client: httpx.AsyncClient, **kwargs) -> AsyncGenerator[BaseContent, None]:
        params = {
            **kwargs,
            "source_id": self.source_id,
            "handle": self.handle,
            "user_id": self.user_id,
            "limit": kwargs.get("limit", self.default_limit),
        }
        async for item in super()._run(client, **params):
            yield item


class AIAtMetaXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_ai_at_meta"
    handle = "AIatMeta"
    user_id = "1034844617261248512"
    name = "X · AI at Meta"
    description = "Meta AI 官方研究与模型动态。"
    source_owner = "meta"
    source_brand = "meta_ai"
    source_scope = "frontier_ai_lab"
    source_url = "https://x.com/AIatMeta"
    content_tags = ["model_release", "research_paper", "open_model"]


class DeepSeekXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_deepseek_ai"
    handle = "deepseek_ai"
    user_id = "1714580962569588736"
    name = "X · DeepSeek"
    description = "DeepSeek 官方模型、论文与服务动态。"
    source_owner = "deepseek"
    source_brand = "deepseek"
    source_scope = "frontier_ai_lab"
    source_url = "https://x.com/deepseek_ai"
    content_tags = ["model_release", "research_paper", "api_platform"]


class QwenXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_alibaba_qwen"
    handle = "Alibaba_Qwen"
    user_id = "1753339277386342400"
    name = "X · Qwen"
    description = "通义千问官方模型、开源与产品动态。"
    source_owner = "alibaba"
    source_brand = "qwen"
    source_scope = "frontier_ai_lab"
    source_url = "https://x.com/Alibaba_Qwen"
    content_tags = ["model_release", "open_model", "developer_tool"]


class MoonshotXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_moonshot_ai"
    handle = "Kimi_Moonshot"
    user_id = "1863959670169501696"
    name = "X · Moonshot AI"
    description = "Moonshot AI / Kimi 官方模型与开源动态。"
    source_owner = "moonshot_ai"
    source_brand = "kimi"
    source_scope = "frontier_ai_lab"
    source_url = "https://x.com/Kimi_Moonshot"
    content_tags = ["model_release", "open_model", "product_update"]


class OpenRouterXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_openrouter"
    # 2026-07 品牌更新后官方站页脚已指向 @openrouter；旧 @OpenRouterAI 已被 X API
    # 判定为 resource-not-found。source_id 保持既定契约不变。
    handle = "openrouter"
    user_id = "1681349314797240320"
    name = "X · OpenRouter"
    description = "OpenRouter 新模型上线与跨厂商事件哨兵。"
    source_owner = "openrouter"
    source_brand = "openrouter"
    source_scope = "model_platform"
    source_url = "https://x.com/openrouter"
    content_tags = ["model_release", "api_platform", "industry_signal"]


class KarpathyXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_karpathy"
    handle = "karpathy"
    user_id = "33836629"
    name = "X · Andrej Karpathy"
    description = "Andrej Karpathy 的模型训练、Agent 与工程观察。"
    source_owner = "andrej_karpathy"
    source_brand = "karpathy"
    source_scope = "expert_commentary"
    source_url = "https://x.com/karpathy"
    content_tags = ["expert_commentary", "developer_tool", "agent"]


class SamAltmanXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_sama"
    handle = "sama"
    user_id = "1605"
    name = "X · Sam Altman"
    description = "Sam Altman 的 OpenAI 战略、发布口风与一手动态。"
    source_owner = "sam_altman"
    source_brand = "openai"
    source_scope = "executive_commentary"
    source_url = "https://x.com/sama"
    content_tags = ["executive_commentary", "model_release", "product_update"]
    noise_risk = "high_noise"


class OpenAIXTimelineFetcher(PresetXTimelineFetcher):
    source_id = "x_openai"
    handle = "OpenAI"
    user_id = "4398626122"
    name = "X · OpenAI"
    description = "OpenAI 官方账号；作为 X 与官网新闻时效/重复率对照组。"
    source_owner = "openai"
    source_brand = "openai"
    source_scope = "frontier_ai_lab"
    source_url = "https://x.com/OpenAI"
    content_tags = ["model_release", "product_update", "research_paper", "api_platform"]
