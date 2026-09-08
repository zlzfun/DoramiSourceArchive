import configparser
import datetime as dt
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PODCAST_STAGES = frozenset({
    "fetch",
    "asr",
    "translate",
    "analyze",
    "digest",
    "script",
    "tts",
    "audio_qa",
    "local_publish",
})
PODCAST_EXTERNAL_DEFAULT_STAGES = (
    "fetch",
    "asr",
    "translate",
    "analyze",
    "digest",
    "script",
    "tts",
    "audio_qa",
    "local_publish",
)
PODCAST_EXTERNAL_DEFAULT_TARGETS = (
    "transcript",
    "digest_blog",
    "digest_audio",
)
PODCAST_EXTERNAL_DEFAULT_MONTHLY_BUDGET_CNY_MINOR = 100_000
PODCAST_EXTERNAL_DEFAULT_PER_RUN_BUDGET_CNY_MINOR = 5_000
PODCAST_EXTERNAL_DEFAULT_VOICE_PROFILE = "narrator_zh"

# One canonical ceiling applies to every Podcast text producer/importer and to
# the Reader's legacy-data projection. Deployments may lower either value via
# config/environment; keeping the defaults named avoids independent magic
# numbers drifting across ingestion and Archive Sync.
DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES = 8 * 1024 * 1024
DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS = 4_000_000
DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS = 1000


def _csv(raw_value: str) -> list[str]:
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _path(raw_value: str) -> str:
    path = Path(raw_value).expanduser()
    if path.is_absolute():
        return str(path)
    return str((PROJECT_ROOT / path).resolve())


def _database_url(raw_value: str) -> str:
    sqlite_prefix = "sqlite:///"
    if raw_value == "sqlite:///:memory:":
        return raw_value
    if raw_value.startswith(sqlite_prefix) and not raw_value.startswith("sqlite:////"):
        return f"{sqlite_prefix}{_path(raw_value[len(sqlite_prefix):])}"
    return raw_value


def _byte_limit(
    parser: configparser.ConfigParser,
    *,
    section: str,
    bytes_option: str,
    mb_option: str,
    bytes_env: str,
    mb_env: str,
    fallback_mb: int,
) -> int:
    """Read an exact byte limit when supplied, otherwise convert configured MiB."""

    raw_bytes = os.getenv(bytes_env)
    if raw_bytes is not None and raw_bytes.strip():
        return int(raw_bytes)
    raw_mb = os.getenv(mb_env)
    if raw_mb is not None and raw_mb.strip():
        return int(raw_mb) * 1024 * 1024
    if parser.has_option(section, bytes_option):
        return parser.getint(section, bytes_option)
    return parser.getint(section, mb_option, fallback=fallback_mb) * 1024 * 1024


@dataclass(frozen=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8088
    # 安全默认：reload 默认关闭，开发环境由 config/backend.ini 显式 `reload = true` 开启；
    # 生产再由 main.py 的 NODE_ENV 守卫兜底强制关闭。避免漏配时误开 reload。
    reload: bool = False


@dataclass(frozen=True)
class RuntimeConfig:
    role: str = "all"


@dataclass(frozen=True)
class TaxonomyDeploymentConfig:
    """Explicit taxonomy deployment posture; never inferred from runtime role."""

    mode: str = "manual"
    catalog_path: str = str(PROJECT_ROOT / "config" / "taxonomy-v1-approved-catalog.json")


@dataclass(frozen=True)
class NetworkConfig:
    disable_ca_bundle: bool = True
    hf_endpoint: str = "https://hf-mirror.com"


@dataclass(frozen=True)
class ProxyConfig:
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "127.0.0.1,localhost"


@dataclass(frozen=True)
class AuthConfig:
    # 账户全部数据库托管（users 表）；ini 不再承载账户名单，首启空表时由
    # accounts.seed_root_admin_if_empty 自动种根管理员 admin/admin。
    cookie_name: str = "dorami_admin_session"
    session_seconds: int = 604800
    secret: Optional[str] = None
    cookie_secure: bool = False


@dataclass(frozen=True)
class StorageConfig:
    database_url: str


@dataclass(frozen=True)
class CorsConfig:
    allow_origins: list[str]
    allow_credentials: bool = True
    allow_methods: list[str] = None
    allow_headers: list[str] = None

    def __post_init__(self):
        if self.allow_methods is None:
            object.__setattr__(self, "allow_methods", ["*"])
        if self.allow_headers is None:
            object.__setattr__(self, "allow_headers", ["*"])


@dataclass(frozen=True)
class LLMConfig:
    """大模型（OpenAI 兼容协议）配置。

    统一走 OpenAI 兼容的 /chat/completions 接口（base_url + api_key + model），
    覆盖 OpenAI/DeepSeek/Kimi/智谱/通义/火山方舟/OpenRouter/Ollama/vLLM 等。
    api_key 为机密，优先从 ini/环境变量读取，运行期 KV 覆盖见 services/daily_brief.py。
    """

    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: int = 60
    temperature: float = 0.3
    max_tokens: int = 4096
    map_concurrency: int = 4
    # 思考模式(opt-in,仅支持该参数的端点可设,如 DeepSeek V4 系):
    # "" = 不发送任何思考参数(默认,兼容一切 OpenAI 兼容端点);
    # "disabled" = 关闭思考;"low"/"high"/"max" = 开启思考并指定努力档。
    # 思考型模型默认开思考且努力档 high,长输出任务(日报 reduce)可能被
    # 思考吃满 max_tokens 而正文空产——生产 2026-08 事故的根因。
    thinking_mode: str = ""
    # 辅助轻模型(可选):同端点同 api_key 下的第二个模型名,供检索规划/选篇/
    # 日报 map/去重聚类这类「轻量结构化调用」使用——主模型走旗舰/思考档时,
    # 这些调用没必要陪跑高延迟高成本。空 = 不启用,全部调用走主模型。
    aux_model: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)

    def for_aux(self) -> "LLMConfig":
        """轻量调用的有效配置:aux_model 已配置且异于主模型时换模型名,
        并且**不下发思考参数**(轻任务输出短小 JSON,思考型默认档反易把
        输出配额耗给思考);未配置时原样返回自身,调用方无需分支。"""
        aux = (self.aux_model or "").strip()
        if not aux or aux == self.model:
            return self
        return replace(self, model=aux, thinking_mode="")


@dataclass(frozen=True)
class XApiConfig:
    """X API v2 公开数据读取配置。

    bearer_token 默认从环境变量/ini 进入进程；管理端可写入运行期
    AppSettingRecord 覆盖，但 API 永不回显明文且不记日志。其余字段用于把
    单次抓取和月度费用锁在小额观察期范围内。
    """

    bearer_token: str = ""
    base_url: str = "https://api.x.com/2"
    timeout_seconds: int = 30
    max_results: int = 25
    monthly_budget_usd: float = 5.0

    @property
    def configured(self) -> bool:
        return bool(self.bearer_token)


@dataclass(frozen=True)
class MediaConfig:
    """媒体库（图床）配置：正文外链图片的本地缓存与代理。

    enabled 关闭时 /api/media/proxy 直接 302 回源、抓取后不做预取——
    行为退回「外链直连」时代。缓存按内容 sha256 去重落盘 media_dir。
    """

    enabled: bool = True
    media_dir: str = ""
    max_file_mb: int = 20
    timeout_seconds: int = 20
    prefetch_concurrency: int = 4


@dataclass(frozen=True)
class PodcastConfig:
    """Podcast execution authority and feed network safety limits."""

    installation: str = "development"
    authority_id: str = "dev-local"
    allowed_stages: tuple[str, ...] = ("fetch", "local_publish")
    feed_max_bytes: int = 20 * 1024 * 1024
    feed_timeout_seconds: int = 30
    transcript_max_bytes: int = 8 * 1024 * 1024
    transcript_timeout_seconds: int = 30
    transcript_max_segments: int = 100_000
    transcript_max_text_chars: int = 4_000_000
    transcript_duration_tolerance_seconds: int = 5
    text_artifact_max_bytes: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES
    text_artifact_max_chars: int = DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS
    text_sync_page_max_bytes: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES
    text_sync_page_max_rows: int = DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS
    reader_text_default_chars: int = 12_000
    reader_text_max_chars: int = 50_000
    reader_text_query_max_chars: int = 200
    # Empty means derive a purpose-separated key from the configured auth
    # secret. Production already requires that auth secret to be explicit.
    reader_cursor_secret: str = ""
    processing_enabled: bool = False
    text_pipeline_version: str = "podcast-text-v1"
    audio_pipeline_version: str = "podcast-audio-v1"
    processing_policy_version: str = "podcast-processing-policy-v1"
    monthly_budget_cny_minor: int = 0
    per_run_budget_cny_minor: int = 0
    budget_scope: str = "podcast-paid-processing"
    budget_timezone: str = "Asia/Shanghai"
    provider_ready_targets: tuple[str, ...] = ()
    voice_profiles: tuple[str, ...] = ()
    default_voice_profile: str = ""
    premium_score_threshold: float = 8.5
    premium_transcript_max_chars: int = 120_000
    premium_blog_max_chars: int = 6_000
    premium_narration_max_chars: int = 4_200
    premium_min_duration_seconds: int = 20 * 60
    premium_max_audio_minutes: int = 15
    premium_guide_mode: str = "solo_preview"

    def __post_init__(self) -> None:
        installation = (self.installation or "").strip().lower()
        authority_id = (self.authority_id or "").strip()
        stages = tuple((stage or "").strip().lower() for stage in self.allowed_stages)
        object.__setattr__(self, "installation", installation)
        object.__setattr__(self, "authority_id", authority_id)
        object.__setattr__(self, "allowed_stages", stages)
        ready_targets = tuple(
            (target or "").strip().lower() for target in self.provider_ready_targets
        )
        voices = tuple((voice or "").strip() for voice in self.voice_profiles)
        default_voice = (self.default_voice_profile or "").strip()
        object.__setattr__(self, "provider_ready_targets", ready_targets)
        object.__setattr__(self, "voice_profiles", voices)
        object.__setattr__(self, "default_voice_profile", default_voice)
        if installation not in {"development", "external", "internal"}:
            raise ValueError(f"unknown Podcast installation: {installation}")
        unknown = sorted(set(stages) - PODCAST_STAGES)
        if unknown:
            raise ValueError(f"unknown Podcast stage(s): {', '.join(unknown)}")
        duplicates = sorted({stage for stage in stages if stages.count(stage) > 1})
        if duplicates:
            raise ValueError(f"duplicate Podcast stage(s): {', '.join(duplicates)}")
        if not authority_id:
            raise ValueError("Podcast authority_id cannot be empty")
        if installation == "internal" and stages:
            raise ValueError(
                "internal installation is sync-only and cannot enable Podcast stages"
            )
        if self.feed_max_bytes <= 0 or self.feed_timeout_seconds <= 0:
            raise ValueError("Podcast feed limits must be positive")
        if (
            self.transcript_max_bytes <= 0
            or self.transcript_timeout_seconds <= 0
            or self.transcript_max_segments <= 0
            or self.transcript_max_text_chars <= 0
            or self.transcript_duration_tolerance_seconds < 0
        ):
            raise ValueError("Podcast transcript limits are invalid")
        if self.text_artifact_max_bytes <= 0 or self.text_artifact_max_chars <= 0:
            raise ValueError("Podcast text artifact limits must be positive")
        if self.text_sync_page_max_bytes <= self.text_artifact_max_bytes:
            raise ValueError(
                "Podcast text sync page byte limit must exceed the artifact byte limit"
            )
        if self.text_sync_page_max_rows <= 0:
            raise ValueError("Podcast text sync page row limit must be positive")
        if (
            self.reader_text_default_chars <= 0
            or self.reader_text_max_chars <= 0
            or self.reader_text_query_max_chars <= 0
            or self.reader_text_default_chars > self.reader_text_max_chars
        ):
            raise ValueError("Podcast Reader text limits are invalid")
        if self.reader_cursor_secret and len(self.reader_cursor_secret) < 32:
            raise ValueError("Podcast Reader cursor secret must contain at least 32 characters")
        if not 1.0 <= self.premium_score_threshold < 10.0:
            raise ValueError("Podcast premium_score_threshold must be in [1, 10)")
        if any(
            value <= 0
            for value in (
                self.premium_transcript_max_chars,
                self.premium_blog_max_chars,
                self.premium_narration_max_chars,
                self.premium_min_duration_seconds,
                self.premium_max_audio_minutes,
            )
        ):
            raise ValueError("Podcast premium guide text limits must be positive")
        if self.premium_guide_mode not in {
            "solo_preview",
            "solo_deep",
            "dual_deep",
        }:
            raise ValueError("Podcast premium_guide_mode is invalid")
        for value, label in (
            (self.text_pipeline_version, "text_pipeline_version"),
            (self.audio_pipeline_version, "audio_pipeline_version"),
            (self.processing_policy_version, "processing_policy_version"),
            (self.budget_scope, "budget_scope"),
            (self.budget_timezone, "budget_timezone"),
        ):
            if not str(value or "").strip():
                raise ValueError(f"Podcast {label} cannot be empty")
        if self.monthly_budget_cny_minor < 0 or self.per_run_budget_cny_minor < 0:
            raise ValueError("Podcast CNY minor-unit budgets cannot be negative")
        if self.per_run_budget_cny_minor > self.monthly_budget_cny_minor:
            raise ValueError("Podcast per-run budget cannot exceed monthly budget")
        try:
            ZoneInfo(self.budget_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Podcast budget_timezone is unknown") from exc
        unknown_targets = sorted(
            set(ready_targets) - {"transcript", "digest_blog", "digest_audio"}
        )
        if unknown_targets:
            raise ValueError(
                f"unknown Podcast provider-ready target(s): {', '.join(unknown_targets)}"
            )
        if len(set(ready_targets)) != len(ready_targets):
            raise ValueError("duplicate Podcast provider-ready target")
        if self.processing_enabled and (
            not ready_targets
            or self.monthly_budget_cny_minor <= 0
            or self.per_run_budget_cny_minor <= 0
        ):
            raise ValueError(
                "enabled Podcast processing requires targets and positive CNY budgets"
            )
        allowed_targets = {
            "external": {"transcript", "digest_blog", "digest_audio"},
            "internal": set(),
            "development": {"transcript", "digest_blog", "digest_audio"},
        }[installation]
        if set(ready_targets) - allowed_targets:
            raise ValueError(
                f"Podcast provider-ready targets conflict with {installation} installation"
            )
        required_target_stages = {
            "transcript": {"asr"},
            "digest_blog": {"asr", "translate", "analyze", "digest", "script"},
            "digest_audio": {"tts", "audio_qa", "local_publish"},
        }
        for target in ready_targets:
            missing = required_target_stages[target] - set(stages)
            if missing:
                raise ValueError(
                    f"Podcast target {target} is missing stage(s): {', '.join(sorted(missing))}"
                )
        if any(not voice for voice in voices) or len(set(voices)) != len(voices):
            raise ValueError("Podcast voice profiles must be nonempty and unique")
        if default_voice and default_voice not in voices:
            raise ValueError("Podcast default voice profile must be registered")
        if "digest_audio" in ready_targets and not default_voice:
            raise ValueError(
                "Podcast digest_audio readiness requires a default voice profile"
            )


@dataclass(frozen=True)
class PodcastWorkerConfig:
    """Bounded scheduler cadence and lease controls for Podcast workers."""

    tick_seconds: int = 10
    lease_seconds: int = 120
    heartbeat_seconds: int = 30
    fallback_retry_seconds: int = 30
    max_steps_per_tick: int = 1

    def __post_init__(self) -> None:
        for name in (
            "tick_seconds",
            "lease_seconds",
            "heartbeat_seconds",
            "fallback_retry_seconds",
            "max_steps_per_tick",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"Podcast worker {name} must be a positive integer")
        if self.max_steps_per_tick > 100:
            raise ValueError("Podcast worker max_steps_per_tick cannot exceed 100")
        if self.heartbeat_seconds >= self.lease_seconds:
            raise ValueError(
                "Podcast worker heartbeat_seconds must be shorter than lease_seconds"
            )


@dataclass(frozen=True)
class PodcastArtifactStorageConfig:
    """Local content-addressed storage for Podcast audio artifacts."""

    root_dir: str = str(PROJECT_ROOT / "data" / "podcast-artifacts")
    max_audio_mb: int = 512
    total_quota_bytes: int = 10_240 * 1024 * 1024
    minimum_free_bytes: int = 1_024 * 1024 * 1024
    allowed_mime_types: tuple[str, ...] = (
        "audio/mpeg",
        "audio/wav",
        "audio/mp4",
        "audio/ogg",
        "audio/webm",
    )
    upload_timeout_seconds: int = 120
    download_timeout_seconds: int = 120
    download_max_redirects: int = 5
    source_audio_ttl_seconds: int = 7 * 24 * 60 * 60
    source_audio_quota_bytes: int = 2_048 * 1024 * 1024
    ffprobe_binary: str = "ffprobe"
    probe_timeout_seconds: int = 15
    orphan_grace_seconds: int = 3600
    staging_ttl_seconds: int = 3600

    def __post_init__(self) -> None:
        if self.max_audio_mb <= 0:
            raise ValueError("Podcast artifact max_audio_mb must be positive")
        if self.total_quota_bytes <= 0:
            raise ValueError("Podcast artifact total quota must be positive")
        if self.minimum_free_bytes < 0:
            raise ValueError("Podcast artifact minimum free bytes cannot be negative")
        if self.upload_timeout_seconds <= 0 or self.download_timeout_seconds <= 0:
            raise ValueError("Podcast artifact timeouts must be positive")
        if self.download_max_redirects < 0:
            raise ValueError(
                "Podcast artifact download_max_redirects cannot be negative"
            )
        if self.source_audio_ttl_seconds <= 0:
            raise ValueError(
                "Podcast artifact source_audio_ttl_seconds must be positive"
            )
        if self.source_audio_quota_bytes <= 0:
            raise ValueError(
                "Podcast artifact source_audio quota must be positive"
            )
        if not self.ffprobe_binary.strip():
            raise ValueError("Podcast artifact ffprobe_binary cannot be empty")
        if self.probe_timeout_seconds <= 0:
            raise ValueError("Podcast artifact probe_timeout_seconds must be positive")
        if self.orphan_grace_seconds < 0:
            raise ValueError("Podcast artifact orphan_grace_seconds cannot be negative")
        if self.staging_ttl_seconds < 0:
            raise ValueError("Podcast artifact staging_ttl_seconds cannot be negative")
        if not self.allowed_mime_types:
            raise ValueError("Podcast artifact allowed_mime_types cannot be empty")


@dataclass(frozen=True)
class PodcastAsrFetchConfig:
    """Short-lived public URL signing used only for provider ASR fetches.

    The public base is deployment-owned and must never be inferred from an
    inbound request Host header.  An empty startup baseline is valid because
    the credential cabinet may provide a runtime KV override; signer creation
    still fails closed until the effective config is complete.
    """

    public_base_url: str = ""
    signing_secret: str = field(default="", repr=False)
    previous_signing_secret: str = field(default="", repr=False)
    url_ttl_seconds: int = 900
    clock_skew_seconds: int = 30
    min_remaining_seconds: int = 300

    def __post_init__(self) -> None:
        base_url = str(self.public_base_url or "").strip()
        secret = str(self.signing_secret or "").strip()
        previous_secret = str(self.previous_signing_secret or "").strip()
        object.__setattr__(self, "public_base_url", base_url)
        object.__setattr__(self, "signing_secret", secret)
        object.__setattr__(self, "previous_signing_secret", previous_secret)
        if base_url:
            try:
                parsed = urlsplit(base_url)
                port = parsed.port
            except ValueError:
                raise ValueError(
                    "Podcast ASR fetch public_base_url must be an HTTPS URL"
                ) from None
            path = parsed.path or "/"
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or not base_url.isascii()
                or any(char.isspace() for char in base_url)
                or "?" in base_url
                or "#" in base_url
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.netloc.rsplit("@", 1)[-1].endswith(":")
                or port == 0
                or not path.startswith("/")
                or "//" in path
                or any(part in {".", ".."} for part in path.split("/"))
                or "%" in path
                or not path.isascii()
            ):
                raise ValueError(
                    "Podcast ASR fetch public_base_url must be an HTTPS URL "
                    "with a canonical path and no credentials, query, or fragment"
                )
        if secret and len(secret.encode("utf-8")) < 32:
            raise ValueError(
                "Podcast ASR fetch signing_secret must contain at least 32 bytes"
            )
        if previous_secret and len(previous_secret.encode("utf-8")) < 32:
            raise ValueError(
                "Podcast ASR fetch previous_signing_secret must contain at least 32 bytes"
            )
        if self.url_ttl_seconds <= 0:
            raise ValueError("Podcast ASR fetch URL TTL must be positive")
        if self.clock_skew_seconds < 0:
            raise ValueError("Podcast ASR fetch clock skew cannot be negative")
        if self.min_remaining_seconds <= 0:
            raise ValueError(
                "Podcast ASR fetch minimum remaining lifetime must be positive"
            )
        if self.min_remaining_seconds > self.url_ttl_seconds:
            raise ValueError(
                "Podcast ASR fetch minimum remaining lifetime cannot exceed URL TTL"
            )

    @property
    def configured(self) -> bool:
        return bool(self.public_base_url and self.signing_secret)


@dataclass(frozen=True)
class AliyunIsiConfig:
    """Aliyun ISI credentials plus overridable protocol constants.

    Secrets are supplied through the process environment or a protected INI;
    the credential registry can provide a local runtime override.  ASR uses
    POP-signed AK/SK requests while TTS uses the short-lived NLS token, so the
    two readiness properties intentionally remain separate.
    """

    access_key_id: str = field(default="", repr=False)
    access_key_secret: str = field(default="", repr=False)
    security_token: str = field(default="", repr=False)
    app_key: str = field(default="", repr=False)
    access_token: str = field(default="", repr=False)
    token_expires_at: int = 0
    region_id: str = "cn-shanghai"
    asr_domain: str = "filetrans.cn-shanghai.aliyuncs.com"
    asr_product: str = "nls-filetrans"
    asr_api_version: str = "2018-08-17"
    asr_task_version: str = "4.0"
    asr_enable_words: bool = True
    asr_auto_split: bool = True
    asr_enable_sample_rate_adaptive: bool = True
    token_url: str = "https://nls-meta.cn-shanghai.aliyuncs.com/"
    tts_url: str = "https://nls-gateway-cn-shanghai.aliyuncs.com/rest/v1/tts/async"
    tts_product: str = "async-long-text-tts"
    tts_api_version: str = "rest-v1"
    tts_device_id: str = "dorami-source-archive"
    tts_voice_profiles_json: str = ""
    tts_result_allowed_host_suffixes: tuple[str, ...] = ()
    tts_max_chars: int = 100_000
    request_timeout_seconds: int = 30
    asr_poll_interval_seconds: int = 10
    tts_poll_interval_seconds: int = 10
    token_refresh_skew_seconds: int = 300
    # Empty accounting scopes/windows and zero limits mean "not configured",
    # never unlimited. Provider workers must require the matching readiness.
    asr_quota_scope: str = ""
    asr_quota_timezone: str = "Asia/Shanghai"
    asr_daily_audio_seconds_limit: int = 0
    asr_entitlement_ends_at: str = ""
    asr_provider_deadline_seconds: int = 0
    asr_price_cny_minor_per_hour: int = 0
    asr_pricing_revision: str = ""
    tts_quota_scope: str = ""
    tts_campaign_id: str = ""
    tts_campaign_starts_at: str = ""
    tts_campaign_ends_at: str = ""
    tts_campaign_character_limit: int = 0
    tts_provider_deadline_seconds: int = 0
    tts_price_cny_minor_per_10000_chars: int = 0
    tts_pricing_revision: str = ""
    # Aliyun does not expose a verified billed-character field in task results.
    # Keep production fail-closed unless operators explicitly accept frozen
    # submitted characters as the conservative settlement basis.
    tts_usage_settlement_mode: str = "manual"

    def __post_init__(self) -> None:
        for field_name in (
            "region_id",
            "asr_domain",
            "asr_product",
            "asr_api_version",
            "asr_task_version",
            "tts_product",
            "tts_api_version",
        ):
            if not str(getattr(self, field_name) or "").strip():
                raise ValueError(f"Aliyun ISI {field_name} cannot be empty")
        if "://" in self.asr_domain or "/" in self.asr_domain:
            raise ValueError("Aliyun ISI asr_domain must be a hostname")
        for field_name in ("token_url", "tts_url"):
            value = str(getattr(self, field_name) or "").strip()
            try:
                parsed = urlsplit(value)
                port = parsed.port
            except ValueError:
                raise ValueError(
                    f"Aliyun ISI {field_name} must be an HTTPS URL"
                ) from None
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or parsed.netloc.rsplit("@", 1)[-1].endswith(":")
                or port == 0
                or (
                    field_name == "token_url"
                    and parsed.path not in {"", "/"}
                )
            ):
                raise ValueError(f"Aliyun ISI {field_name} must be an HTTPS URL")
        if (
            not self.tts_device_id
            or len(self.tts_device_id) > 128
            or not re.fullmatch(r"[A-Za-z0-9._-]+", self.tts_device_id)
        ):
            raise ValueError("Aliyun ISI tts_device_id is invalid")
        if len(self.tts_voice_profiles_json) > 64 * 1024:
            raise ValueError("Aliyun ISI TTS voice profile configuration is too large")
        if isinstance(self.tts_result_allowed_host_suffixes, str):
            raise ValueError("Aliyun ISI TTS result host suffixes are invalid")
        tts_host_suffixes = tuple(
            str(suffix or "").strip().lower().lstrip(".")
            for suffix in self.tts_result_allowed_host_suffixes
        )
        if any(
            not suffix
            or len(suffix) > 253
            or "." not in suffix
            or any(
                not re.fullmatch(
                    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
                    label,
                )
                for label in suffix.split(".")
            )
            for suffix in tts_host_suffixes
        ) or len(set(tts_host_suffixes)) != len(tts_host_suffixes):
            raise ValueError("Aliyun ISI TTS result host suffixes are invalid")
        object.__setattr__(
            self, "tts_result_allowed_host_suffixes", tts_host_suffixes
        )
        if self.token_expires_at < 0:
            raise ValueError("Aliyun ISI token_expires_at cannot be negative")
        if (
            self.request_timeout_seconds <= 0
            or self.asr_poll_interval_seconds <= 0
            or self.tts_poll_interval_seconds <= 0
        ):
            raise ValueError("Aliyun ISI request and polling intervals must be positive")
        if self.tts_max_chars <= 0 or self.tts_max_chars > 100_000:
            raise ValueError("Aliyun ISI tts_max_chars must be between 1 and 100000")
        if self.token_refresh_skew_seconds < 0:
            raise ValueError("Aliyun ISI token refresh skew cannot be negative")
        for field_name in (
            "asr_daily_audio_seconds_limit",
            "asr_provider_deadline_seconds",
            "asr_price_cny_minor_per_hour",
            "tts_campaign_character_limit",
            "tts_provider_deadline_seconds",
            "tts_price_cny_minor_per_10000_chars",
        ):
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Aliyun ISI {field_name} must be a nonnegative integer")
        try:
            ZoneInfo(self.asr_quota_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Aliyun ISI asr_quota_timezone is unknown") from exc
        for field_name in (
            "asr_entitlement_ends_at",
            "tts_campaign_starts_at",
            "tts_campaign_ends_at",
        ):
            raw = str(getattr(self, field_name) or "").strip()
            object.__setattr__(self, field_name, raw)
            if raw:
                try:
                    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError(
                        f"Aliyun ISI {field_name} must be an RFC3339 timestamp"
                    ) from exc
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    raise ValueError(
                        f"Aliyun ISI {field_name} must include a timezone offset"
                    )
        if self.tts_campaign_starts_at and self.tts_campaign_ends_at:
            starts = dt.datetime.fromisoformat(
                self.tts_campaign_starts_at.replace("Z", "+00:00")
            )
            ends = dt.datetime.fromisoformat(
                self.tts_campaign_ends_at.replace("Z", "+00:00")
            )
            if starts >= ends:
                raise ValueError("Aliyun ISI TTS campaign window is invalid")
        settlement_mode = str(self.tts_usage_settlement_mode or "").strip().lower()
        if settlement_mode not in {"manual", "submitted_characters"}:
            raise ValueError(
                "Aliyun ISI tts_usage_settlement_mode must be manual or "
                "submitted_characters"
            )
        object.__setattr__(self, "tts_usage_settlement_mode", settlement_mode)
        if not all(
            isinstance(value, bool)
            for value in (
                self.asr_enable_words,
                self.asr_auto_split,
                self.asr_enable_sample_rate_adaptive,
            )
        ):
            raise ValueError("Aliyun ISI ASR feature switches must be boolean")
        if self.asr_task_version != "4.0" and (
            self.asr_enable_words or self.asr_enable_sample_rate_adaptive
        ):
            raise ValueError(
                "Aliyun ISI ASR words and sample-rate adaptation require task version 4.0"
            )

    @property
    def ak_configured(self) -> bool:
        return bool(self.access_key_id and self.access_key_secret)

    @property
    def asr_poll_configured(self) -> bool:
        """Whether an existing ASR TaskId can be queried safely."""

        return self.ak_configured

    @property
    def asr_configured(self) -> bool:
        return bool(self.asr_poll_configured and self.app_key)

    @property
    def asr_accounting_ready(self) -> bool:
        return bool(
            self.asr_quota_scope.strip()
            and self.asr_quota_timezone == "Asia/Shanghai"
            and self.asr_daily_audio_seconds_limit > 0
            and self.asr_entitlement_ends_at
            and self.asr_provider_deadline_seconds > 0
            and self.asr_pricing_revision.strip()
        )

    @property
    def tts_configured(self) -> bool:
        # A missing or expired token can be refreshed when AK/SK are present.
        return bool(
            self.app_key
            and (
                self.ak_configured
                or self.token_is_valid_at(int(time.time()))
            )
        )

    @property
    def tts_accounting_ready(self) -> bool:
        return bool(
            self.tts_quota_scope.strip()
            and self.tts_campaign_id.strip()
            and self.tts_campaign_starts_at
            and self.tts_campaign_ends_at
            and self.tts_campaign_character_limit > 0
            and self.tts_provider_deadline_seconds > 0
            and self.tts_pricing_revision.strip()
        )

    def token_is_valid_at(self, unix_seconds: int) -> bool:
        return bool(
            self.access_token
            and self.token_expires_at
            > int(unix_seconds) + self.token_refresh_skew_seconds
        )


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    runtime: RuntimeConfig
    taxonomy: TaxonomyDeploymentConfig
    network: NetworkConfig
    proxy: ProxyConfig
    auth: AuthConfig
    storage: StorageConfig
    cors: CorsConfig
    llm: LLMConfig
    x_api: XApiConfig
    media: MediaConfig
    podcast: PodcastConfig
    podcast_worker: PodcastWorkerConfig
    podcast_artifacts: PodcastArtifactStorageConfig
    podcast_asr_fetch: PodcastAsrFetchConfig
    aliyun_isi: AliyunIsiConfig

    def apply_process_environment(self) -> None:
        if self.network.disable_ca_bundle:
            os.environ["CURL_CA_BUNDLE"] = ""
            os.environ["REQUESTS_CA_BUNDLE"] = ""
        if self.network.hf_endpoint:
            os.environ["HF_ENDPOINT"] = self.network.hf_endpoint
        proxy_values = {
            "HTTP_PROXY": self.proxy.http_proxy,
            "HTTPS_PROXY": self.proxy.https_proxy,
            "NO_PROXY": self.proxy.no_proxy,
        }
        for key, value in proxy_values.items():
            os.environ[key] = value
            os.environ[key.lower()] = value


def _candidate_config_paths() -> list[Path]:
    configured = os.getenv("DORAMI_CONFIG_FILE", "").strip()
    if configured:
        return [Path(configured).expanduser()]
    return [PROJECT_ROOT / "config" / "backend.ini"]


def _read_config_file() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    for path in _candidate_config_paths():
        if path.exists():
            parser.read(path, encoding="utf-8")
            break
    return parser


def _runtime_role(raw_value: str) -> str:
    role = (raw_value or "all").strip().lower()
    allowed = {"all", "collector", "reader"}
    if role not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(f"Invalid runtime role '{raw_value}'. Expected one of: {allowed_text}")
    return role


def _taxonomy_deployment_mode(raw_value: str) -> str:
    mode = (raw_value or "manual").strip().lower()
    allowed = {"authority", "manual", "replica"}
    if mode not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise ValueError(
            f"Invalid taxonomy deployment mode '{raw_value}'. Expected one of: {allowed_text}"
        )
    return mode


def _environment_boolean(name: str) -> bool | None:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}")


def load_config() -> AppConfig:
    parser = _read_config_file()

    storage_db = f"sqlite:///{PROJECT_ROOT / 'data' / 'cms_data.db'}"
    runtime_role = os.getenv("DORAMI_RUNTIME_ROLE") or parser.get("runtime", "role", fallback="all")
    media_enabled_raw = os.getenv("DORAMI_MEDIA_ENABLED")
    if media_enabled_raw is None:
        media_enabled = parser.getboolean("media", "enabled", fallback=True)
    else:
        media_enabled = media_enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
    asr_enable_words = _environment_boolean(
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS"
    )
    asr_auto_split = _environment_boolean(
        "DORAMI_ALIYUN_ISI_ASR_AUTO_SPLIT"
    )
    asr_enable_sample_rate_adaptive = _environment_boolean(
        "DORAMI_ALIYUN_ISI_ASR_ENABLE_SAMPLE_RATE_ADAPTIVE"
    )
    podcast_allowed_mime_types = tuple(_csv(
        os.getenv("DORAMI_PODCAST_ARTIFACT_ALLOWED_MIME_TYPES")
        or parser.get(
            "podcast_artifacts",
            "allowed_mime_types",
            fallback="audio/mpeg,audio/wav,audio/mp4,audio/ogg,audio/webm",
        )
    ))
    podcast_ini_installation = parser.get(
        "podcast", "installation", fallback="development"
    ).strip().lower()
    podcast_installation_env = os.getenv("DORAMI_PODCAST_INSTALLATION")
    podcast_installation = (
        podcast_installation_env or podcast_ini_installation
    ).strip().lower()
    podcast_installation_overridden = bool(
        podcast_installation_env and podcast_installation_env.strip()
    ) and podcast_installation != podcast_ini_installation
    external_podcast = podcast_installation == "external"
    podcast_default_stages = (
        PODCAST_EXTERNAL_DEFAULT_STAGES
        if external_podcast
        else (
            ()
            if podcast_installation == "internal"
            else ("fetch", "local_publish")
        )
    )

    def _podcast_setting(env_name: str, option: str, default: str) -> str:
        environment_value = os.getenv(env_name)
        if environment_value is not None and environment_value.strip():
            return environment_value
        if podcast_installation_overridden:
            # Settings in an INI posture belong to that installation. When an
            # environment override switches hosts, use the new host's defaults.
            return default
        return parser.get("podcast", option, fallback=default)

    podcast_stages_raw = _podcast_setting(
        "DORAMI_PODCAST_ALLOWED_STAGES",
        "allowed_stages",
        ",".join(podcast_default_stages),
    )
    podcast_processing_env = os.getenv("DORAMI_PODCAST_PROCESSING_ENABLED")
    if podcast_processing_env is not None and podcast_processing_env.strip():
        podcast_processing_enabled = podcast_processing_env.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    elif podcast_installation_overridden:
        podcast_processing_enabled = external_podcast
    else:
        podcast_processing_enabled = parser.getboolean(
            "podcast", "processing_enabled", fallback=external_podcast
        )
    podcast_targets_raw = _podcast_setting(
        "DORAMI_PODCAST_PROVIDER_READY_TARGETS",
        "provider_ready_targets",
        ",".join(PODCAST_EXTERNAL_DEFAULT_TARGETS) if external_podcast else "",
    )
    podcast_monthly_budget_raw = _podcast_setting(
        "DORAMI_PODCAST_MONTHLY_BUDGET_CNY_MINOR",
        "monthly_budget_cny_minor",
        str(
            PODCAST_EXTERNAL_DEFAULT_MONTHLY_BUDGET_CNY_MINOR
            if external_podcast
            else 0
        ),
    )
    podcast_per_run_budget_raw = _podcast_setting(
        "DORAMI_PODCAST_PER_RUN_BUDGET_CNY_MINOR",
        "per_run_budget_cny_minor",
        str(
            PODCAST_EXTERNAL_DEFAULT_PER_RUN_BUDGET_CNY_MINOR
            if external_podcast
            else 0
        ),
    )
    podcast_default_voice = (
        PODCAST_EXTERNAL_DEFAULT_VOICE_PROFILE if external_podcast else ""
    )
    podcast_voice_profiles_raw = _podcast_setting(
        "DORAMI_PODCAST_VOICE_PROFILES",
        "voice_profiles",
        podcast_default_voice,
    )
    podcast_default_voice_raw = _podcast_setting(
        "DORAMI_PODCAST_DEFAULT_VOICE_PROFILE",
        "default_voice_profile",
        podcast_default_voice,
    )
    return AppConfig(
        server=ServerConfig(
            host=parser.get("server", "host", fallback="127.0.0.1"),
            port=parser.getint("server", "port", fallback=8088),
            reload=parser.getboolean("server", "reload", fallback=True),
        ),
        runtime=RuntimeConfig(
            role=_runtime_role(runtime_role),
        ),
        taxonomy=TaxonomyDeploymentConfig(
            mode=_taxonomy_deployment_mode(
                os.getenv("DORAMI_TAXONOMY_DEPLOYMENT")
                or parser.get("taxonomy", "deployment", fallback="manual")
            ),
            catalog_path=_path(
                os.getenv("DORAMI_TAXONOMY_CATALOG")
                or parser.get(
                    "taxonomy",
                    "catalog",
                    fallback=str(PROJECT_ROOT / "config" / "taxonomy-v1-approved-catalog.json"),
                )
            ),
        ),
        network=NetworkConfig(
            disable_ca_bundle=parser.getboolean("network", "disable_ca_bundle", fallback=True),
            hf_endpoint=parser.get("network", "hf_endpoint", fallback="https://hf-mirror.com"),
        ),
        proxy=ProxyConfig(
            http_proxy=parser.get("proxy", "http_proxy", fallback=""),
            https_proxy=parser.get("proxy", "https_proxy", fallback=""),
            no_proxy=parser.get("proxy", "no_proxy", fallback="127.0.0.1,localhost"),
        ),
        auth=AuthConfig(
            cookie_name=parser.get("auth", "cookie_name", fallback="dorami_admin_session"),
            session_seconds=parser.getint("auth", "session_seconds", fallback=604800),
            secret=parser.get("auth", "secret", fallback="").strip() or None,
            cookie_secure=parser.getboolean("auth", "cookie_secure", fallback=False),
        ),
        storage=StorageConfig(
            database_url=_database_url(parser.get("storage", "database_url", fallback=storage_db)),
        ),
        cors=CorsConfig(
            allow_origins=_csv(parser.get("cors", "allow_origins", fallback="*")),
            allow_credentials=parser.getboolean("cors", "allow_credentials", fallback=True),
            allow_methods=_csv(parser.get("cors", "allow_methods", fallback="*")),
            allow_headers=_csv(parser.get("cors", "allow_headers", fallback="*")),
        ),
        media=MediaConfig(
            enabled=media_enabled,
            media_dir=_path(parser.get("media", "media_dir", fallback=str(PROJECT_ROOT / "data" / "media"))),
            max_file_mb=parser.getint("media", "max_file_mb", fallback=20),
            timeout_seconds=parser.getint("media", "timeout_seconds", fallback=20),
            prefetch_concurrency=parser.getint("media", "prefetch_concurrency", fallback=4),
        ),
        podcast=PodcastConfig(
            installation=podcast_installation,
            authority_id=(
                os.getenv("DORAMI_PODCAST_AUTHORITY_ID")
                or parser.get("podcast", "authority_id", fallback="dev-local")
            ),
            allowed_stages=tuple(_csv(podcast_stages_raw)),
            feed_max_bytes=int(
                os.getenv("DORAMI_PODCAST_FEED_MAX_BYTES")
                or parser.getint("podcast", "feed_max_bytes", fallback=20 * 1024 * 1024)
            ),
            feed_timeout_seconds=int(
                os.getenv("DORAMI_PODCAST_FEED_TIMEOUT_SECONDS")
                or parser.getint("podcast", "feed_timeout_seconds", fallback=30)
            ),
            transcript_max_bytes=int(
                os.getenv("DORAMI_PODCAST_TRANSCRIPT_MAX_BYTES")
                or parser.getint(
                    "podcast", "transcript_max_bytes", fallback=8 * 1024 * 1024
                )
            ),
            transcript_timeout_seconds=int(
                os.getenv("DORAMI_PODCAST_TRANSCRIPT_TIMEOUT_SECONDS")
                or parser.getint(
                    "podcast", "transcript_timeout_seconds", fallback=30
                )
            ),
            transcript_max_segments=int(
                os.getenv("DORAMI_PODCAST_TRANSCRIPT_MAX_SEGMENTS")
                or parser.getint(
                    "podcast", "transcript_max_segments", fallback=100_000
                )
            ),
            transcript_max_text_chars=int(
                os.getenv("DORAMI_PODCAST_TRANSCRIPT_MAX_TEXT_CHARS")
                or parser.getint(
                    "podcast", "transcript_max_text_chars", fallback=4_000_000
                )
            ),
            transcript_duration_tolerance_seconds=int(
                os.getenv("DORAMI_PODCAST_TRANSCRIPT_DURATION_TOLERANCE_SECONDS")
                or parser.getint(
                    "podcast",
                    "transcript_duration_tolerance_seconds",
                    fallback=5,
                )
            ),
            text_artifact_max_bytes=int(
                os.getenv("DORAMI_PODCAST_TEXT_ARTIFACT_MAX_BYTES")
                or parser.getint(
                    "podcast",
                    "text_artifact_max_bytes",
                    fallback=DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_BYTES,
                )
            ),
            text_artifact_max_chars=int(
                os.getenv("DORAMI_PODCAST_TEXT_ARTIFACT_MAX_CHARS")
                or parser.getint(
                    "podcast",
                    "text_artifact_max_chars",
                    fallback=DEFAULT_PODCAST_TEXT_ARTIFACT_MAX_CHARS,
                )
            ),
            text_sync_page_max_bytes=int(
                os.getenv("DORAMI_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES")
                or parser.getint(
                    "podcast",
                    "text_sync_page_max_bytes",
                    fallback=DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_BYTES,
                )
            ),
            text_sync_page_max_rows=int(
                os.getenv("DORAMI_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS")
                or parser.getint(
                    "podcast",
                    "text_sync_page_max_rows",
                    fallback=DEFAULT_PODCAST_TEXT_SYNC_PAGE_MAX_ROWS,
                )
            ),
            reader_text_default_chars=int(
                os.getenv("DORAMI_PODCAST_READER_TEXT_DEFAULT_CHARS")
                or parser.getint(
                    "podcast", "reader_text_default_chars", fallback=12_000
                )
            ),
            reader_text_max_chars=int(
                os.getenv("DORAMI_PODCAST_READER_TEXT_MAX_CHARS")
                or parser.getint(
                    "podcast", "reader_text_max_chars", fallback=50_000
                )
            ),
            reader_text_query_max_chars=int(
                os.getenv("DORAMI_PODCAST_READER_TEXT_QUERY_MAX_CHARS")
                or parser.getint(
                    "podcast", "reader_text_query_max_chars", fallback=200
                )
            ),
            reader_cursor_secret=(
                os.getenv("DORAMI_PODCAST_READER_CURSOR_SECRET")
                or parser.get("podcast", "reader_cursor_secret", fallback="")
            ).strip(),
            processing_enabled=podcast_processing_enabled,
            text_pipeline_version=(
                os.getenv("DORAMI_PODCAST_TEXT_PIPELINE_VERSION")
                or parser.get(
                    "podcast", "text_pipeline_version", fallback="podcast-text-v1"
                )
            ),
            audio_pipeline_version=(
                os.getenv("DORAMI_PODCAST_AUDIO_PIPELINE_VERSION")
                or parser.get(
                    "podcast", "audio_pipeline_version", fallback="podcast-audio-v1"
                )
            ),
            processing_policy_version=(
                os.getenv("DORAMI_PODCAST_PROCESSING_POLICY_VERSION")
                or parser.get(
                    "podcast",
                    "processing_policy_version",
                    fallback="podcast-processing-policy-v1",
                )
            ),
            monthly_budget_cny_minor=int(podcast_monthly_budget_raw),
            per_run_budget_cny_minor=int(podcast_per_run_budget_raw),
            budget_scope=(
                os.getenv("DORAMI_PODCAST_BUDGET_SCOPE")
                or parser.get(
                    "podcast", "budget_scope", fallback="podcast-paid-processing"
                )
            ),
            budget_timezone=(
                os.getenv("DORAMI_PODCAST_BUDGET_TIMEZONE")
                or parser.get("podcast", "budget_timezone", fallback="Asia/Shanghai")
            ),
            provider_ready_targets=tuple(_csv(podcast_targets_raw)),
            voice_profiles=tuple(_csv(podcast_voice_profiles_raw)),
            default_voice_profile=podcast_default_voice_raw,
            premium_score_threshold=float(
                os.getenv("DORAMI_PODCAST_PREMIUM_SCORE_THRESHOLD")
                or parser.getfloat(
                    "podcast", "premium_score_threshold", fallback=8.5
                )
            ),
            premium_transcript_max_chars=int(
                os.getenv("DORAMI_PODCAST_PREMIUM_TRANSCRIPT_MAX_CHARS")
                or parser.getint(
                    "podcast", "premium_transcript_max_chars", fallback=120_000
                )
            ),
            premium_blog_max_chars=int(
                os.getenv("DORAMI_PODCAST_PREMIUM_BLOG_MAX_CHARS")
                or parser.getint(
                    "podcast", "premium_blog_max_chars", fallback=6_000
                )
            ),
            premium_narration_max_chars=int(
                os.getenv("DORAMI_PODCAST_PREMIUM_NARRATION_MAX_CHARS")
                or parser.getint(
                    "podcast", "premium_narration_max_chars", fallback=4_200
                )
            ),
            premium_min_duration_seconds=int(
                os.getenv("DORAMI_PODCAST_PREMIUM_MIN_DURATION_SECONDS")
                or parser.getint(
                    "podcast", "premium_min_duration_seconds", fallback=20 * 60
                )
            ),
            premium_max_audio_minutes=int(
                os.getenv("DORAMI_PODCAST_PREMIUM_MAX_AUDIO_MINUTES")
                or parser.getint(
                    "podcast", "premium_max_audio_minutes", fallback=15
                )
            ),
            premium_guide_mode=(
                os.getenv("DORAMI_PODCAST_PREMIUM_GUIDE_MODE")
                or parser.get("podcast", "premium_guide_mode", fallback="solo_preview")
            ).strip(),
        ),
        podcast_worker=PodcastWorkerConfig(
            tick_seconds=int(
                os.getenv("DORAMI_PODCAST_WORKER_TICK_SECONDS")
                or parser.getint("podcast_worker", "tick_seconds", fallback=10)
            ),
            lease_seconds=int(
                os.getenv("DORAMI_PODCAST_WORKER_LEASE_SECONDS")
                or parser.getint("podcast_worker", "lease_seconds", fallback=120)
            ),
            heartbeat_seconds=int(
                os.getenv("DORAMI_PODCAST_WORKER_HEARTBEAT_SECONDS")
                or parser.getint(
                    "podcast_worker", "heartbeat_seconds", fallback=30
                )
            ),
            fallback_retry_seconds=int(
                os.getenv("DORAMI_PODCAST_WORKER_FALLBACK_RETRY_SECONDS")
                or parser.getint(
                    "podcast_worker", "fallback_retry_seconds", fallback=30
                )
            ),
            max_steps_per_tick=int(
                os.getenv("DORAMI_PODCAST_WORKER_MAX_STEPS_PER_TICK")
                or parser.getint(
                    "podcast_worker", "max_steps_per_tick", fallback=1
                )
            ),
        ),
        podcast_artifacts=PodcastArtifactStorageConfig(
            root_dir=_path(
                os.getenv("DORAMI_PODCAST_ARTIFACT_ROOT_DIR")
                or parser.get(
                    "podcast_artifacts",
                    "root_dir",
                    fallback=str(PROJECT_ROOT / "data" / "podcast-artifacts"),
                )
            ),
            max_audio_mb=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_MAX_AUDIO_MB")
                or parser.getint("podcast_artifacts", "max_audio_mb", fallback=512)
            ),
            total_quota_bytes=_byte_limit(
                parser,
                section="podcast_artifacts",
                bytes_option="total_quota_bytes",
                mb_option="total_quota_mb",
                bytes_env="DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_BYTES",
                mb_env="DORAMI_PODCAST_ARTIFACT_TOTAL_QUOTA_MB",
                fallback_mb=10_240,
            ),
            minimum_free_bytes=_byte_limit(
                parser,
                section="podcast_artifacts",
                bytes_option="minimum_free_bytes",
                mb_option="minimum_free_mb",
                bytes_env="DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_BYTES",
                mb_env="DORAMI_PODCAST_ARTIFACT_MINIMUM_FREE_MB",
                fallback_mb=1_024,
            ),
            allowed_mime_types=podcast_allowed_mime_types,
            upload_timeout_seconds=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_UPLOAD_TIMEOUT_SECONDS")
                or parser.getint(
                    "podcast_artifacts", "upload_timeout_seconds", fallback=120
                )
            ),
            download_timeout_seconds=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_DOWNLOAD_TIMEOUT_SECONDS")
                or parser.getint(
                    "podcast_artifacts", "download_timeout_seconds", fallback=120
                )
            ),
            download_max_redirects=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_DOWNLOAD_MAX_REDIRECTS")
                or parser.getint(
                    "podcast_artifacts", "download_max_redirects", fallback=5
                )
            ),
            source_audio_ttl_seconds=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_TTL_SECONDS")
                or parser.getint(
                    "podcast_artifacts", "source_audio_ttl_seconds", fallback=604800
                )
            ),
            source_audio_quota_bytes=_byte_limit(
                parser,
                section="podcast_artifacts",
                bytes_option="source_audio_quota_bytes",
                mb_option="source_audio_quota_mb",
                bytes_env="DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_QUOTA_BYTES",
                mb_env="DORAMI_PODCAST_ARTIFACT_SOURCE_AUDIO_QUOTA_MB",
                fallback_mb=2_048,
            ),
            ffprobe_binary=(
                os.getenv("DORAMI_PODCAST_ARTIFACT_FFPROBE_BINARY")
                or parser.get("podcast_artifacts", "ffprobe_binary", fallback="ffprobe")
            ).strip(),
            probe_timeout_seconds=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_PROBE_TIMEOUT_SECONDS")
                or parser.getint(
                    "podcast_artifacts", "probe_timeout_seconds", fallback=15
                )
            ),
            orphan_grace_seconds=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_ORPHAN_GRACE_SECONDS")
                or parser.getint(
                    "podcast_artifacts", "orphan_grace_seconds", fallback=3600
                )
            ),
            staging_ttl_seconds=int(
                os.getenv("DORAMI_PODCAST_ARTIFACT_STAGING_TTL_SECONDS")
                or parser.getint(
                    "podcast_artifacts", "staging_ttl_seconds", fallback=3600
                )
            ),
        ),
        podcast_asr_fetch=PodcastAsrFetchConfig(
            public_base_url=(
                os.getenv("DORAMI_PODCAST_ASR_FETCH_PUBLIC_BASE_URL")
                or parser.get(
                    "podcast_asr_fetch", "public_base_url", fallback=""
                )
            ).strip(),
            signing_secret=(
                os.getenv("DORAMI_PODCAST_ASR_FETCH_SIGNING_SECRET")
                or parser.get(
                    "podcast_asr_fetch", "signing_secret", fallback=""
                )
            ),
            previous_signing_secret=(
                os.getenv("DORAMI_PODCAST_ASR_FETCH_PREVIOUS_SIGNING_SECRET")
                or parser.get(
                    "podcast_asr_fetch",
                    "previous_signing_secret",
                    fallback="",
                )
            ),
            url_ttl_seconds=int(
                os.getenv("DORAMI_PODCAST_ASR_FETCH_URL_TTL_SECONDS")
                or parser.getint(
                    "podcast_asr_fetch", "url_ttl_seconds", fallback=900
                )
            ),
            clock_skew_seconds=int(
                os.getenv("DORAMI_PODCAST_ASR_FETCH_CLOCK_SKEW_SECONDS")
                or parser.getint(
                    "podcast_asr_fetch", "clock_skew_seconds", fallback=30
                )
            ),
            min_remaining_seconds=int(
                os.getenv("DORAMI_PODCAST_ASR_FETCH_MIN_REMAINING_SECONDS")
                or parser.getint(
                    "podcast_asr_fetch", "min_remaining_seconds", fallback=300
                )
            ),
        ),
        aliyun_isi=AliyunIsiConfig(
            access_key_id=(
                os.getenv("ALIYUN_AK_ID")
                or parser.get("aliyun_isi", "access_key_id", fallback="")
            ).strip(),
            access_key_secret=(
                os.getenv("ALIYUN_AK_SECRET")
                or parser.get("aliyun_isi", "access_key_secret", fallback="")
            ).strip(),
            security_token=(
                os.getenv("ALIYUN_SECURITY_TOKEN")
                or parser.get("aliyun_isi", "security_token", fallback="")
            ).strip(),
            app_key=(
                os.getenv("NLS_APP_KEY")
                or parser.get("aliyun_isi", "app_key", fallback="")
            ).strip(),
            access_token=(
                os.getenv("NLS_ACCESS_TOKEN")
                or parser.get("aliyun_isi", "access_token", fallback="")
            ).strip(),
            token_expires_at=int(
                os.getenv("NLS_TOKEN_EXPIRES_AT")
                or parser.getint("aliyun_isi", "token_expires_at", fallback=0)
            ),
            region_id=(
                os.getenv("DORAMI_ALIYUN_ISI_REGION_ID")
                or parser.get("aliyun_isi", "region_id", fallback="cn-shanghai")
            ).strip(),
            asr_domain=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_DOMAIN")
                or parser.get(
                    "aliyun_isi",
                    "asr_domain",
                    fallback="filetrans.cn-shanghai.aliyuncs.com",
                )
            ).strip(),
            asr_product=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_PRODUCT")
                or parser.get("aliyun_isi", "asr_product", fallback="nls-filetrans")
            ).strip(),
            asr_api_version=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_API_VERSION")
                or parser.get("aliyun_isi", "asr_api_version", fallback="2018-08-17")
            ).strip(),
            asr_task_version=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_TASK_VERSION")
                or parser.get("aliyun_isi", "asr_task_version", fallback="4.0")
            ).strip(),
            asr_enable_words=(
                asr_enable_words
                if asr_enable_words is not None
                else parser.getboolean(
                    "aliyun_isi", "asr_enable_words", fallback=True
                )
            ),
            asr_auto_split=(
                asr_auto_split
                if asr_auto_split is not None
                else parser.getboolean(
                    "aliyun_isi", "asr_auto_split", fallback=True
                )
            ),
            asr_enable_sample_rate_adaptive=(
                asr_enable_sample_rate_adaptive
                if asr_enable_sample_rate_adaptive is not None
                else parser.getboolean(
                    "aliyun_isi",
                    "asr_enable_sample_rate_adaptive",
                    fallback=True,
                )
            ),
            token_url=(
                os.getenv("DORAMI_ALIYUN_ISI_TOKEN_URL")
                or parser.get(
                    "aliyun_isi",
                    "token_url",
                    fallback="https://nls-meta.cn-shanghai.aliyuncs.com/",
                )
            ).strip(),
            tts_url=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_URL")
                or parser.get(
                    "aliyun_isi",
                    "tts_url",
                    fallback=(
                        "https://nls-gateway-cn-shanghai.aliyuncs.com/"
                        "rest/v1/tts/async"
                    ),
                )
            ).strip(),
            tts_product=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_PRODUCT")
                or parser.get(
                    "aliyun_isi",
                    "tts_product",
                    fallback="async-long-text-tts",
                )
            ).strip(),
            tts_api_version=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_API_VERSION")
                or parser.get(
                    "aliyun_isi", "tts_api_version", fallback="rest-v1"
                )
            ).strip(),
            tts_device_id=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_DEVICE_ID")
                or parser.get(
                    "aliyun_isi",
                    "tts_device_id",
                    fallback="dorami-source-archive",
                )
            ).strip(),
            tts_voice_profiles_json=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_VOICE_PROFILES_JSON")
                or parser.get(
                    "aliyun_isi",
                    "tts_voice_profiles_json",
                    fallback="",
                )
            ).strip(),
            tts_result_allowed_host_suffixes=tuple(
                _csv(
                    os.getenv("DORAMI_ALIYUN_ISI_TTS_RESULT_ALLOWED_HOST_SUFFIXES")
                    or parser.get(
                        "aliyun_isi",
                        "tts_result_allowed_host_suffixes",
                        fallback="",
                    )
                )
            ),
            tts_max_chars=int(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_MAX_CHARS")
                or parser.getint("aliyun_isi", "tts_max_chars", fallback=100_000)
            ),
            request_timeout_seconds=int(
                os.getenv("DORAMI_ALIYUN_ISI_REQUEST_TIMEOUT_SECONDS")
                or parser.getint("aliyun_isi", "request_timeout_seconds", fallback=30)
            ),
            asr_poll_interval_seconds=int(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_POLL_INTERVAL_SECONDS")
                or parser.getint(
                    "aliyun_isi", "asr_poll_interval_seconds", fallback=10
                )
            ),
            tts_poll_interval_seconds=int(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_POLL_INTERVAL_SECONDS")
                or parser.getint(
                    "aliyun_isi", "tts_poll_interval_seconds", fallback=10
                )
            ),
            token_refresh_skew_seconds=int(
                os.getenv("DORAMI_ALIYUN_ISI_TOKEN_REFRESH_SKEW_SECONDS")
                or parser.getint(
                    "aliyun_isi", "token_refresh_skew_seconds", fallback=300
                )
            ),
            asr_quota_scope=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_QUOTA_SCOPE")
                or parser.get("aliyun_isi", "asr_quota_scope", fallback="")
            ).strip(),
            asr_quota_timezone=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_QUOTA_TIMEZONE")
                or parser.get(
                    "aliyun_isi", "asr_quota_timezone", fallback="Asia/Shanghai"
                )
            ).strip(),
            asr_daily_audio_seconds_limit=int(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_DAILY_AUDIO_SECONDS_LIMIT")
                or parser.getint(
                    "aliyun_isi", "asr_daily_audio_seconds_limit", fallback=0
                )
            ),
            asr_entitlement_ends_at=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_ENTITLEMENT_ENDS_AT")
                or parser.get("aliyun_isi", "asr_entitlement_ends_at", fallback="")
            ).strip(),
            asr_provider_deadline_seconds=int(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_PROVIDER_DEADLINE_SECONDS")
                or parser.getint(
                    "aliyun_isi", "asr_provider_deadline_seconds", fallback=0
                )
            ),
            asr_price_cny_minor_per_hour=int(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_PRICE_CNY_MINOR_PER_HOUR")
                or parser.getint(
                    "aliyun_isi", "asr_price_cny_minor_per_hour", fallback=0
                )
            ),
            asr_pricing_revision=(
                os.getenv("DORAMI_ALIYUN_ISI_ASR_PRICING_REVISION")
                or parser.get("aliyun_isi", "asr_pricing_revision", fallback="")
            ).strip(),
            tts_quota_scope=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_QUOTA_SCOPE")
                or parser.get("aliyun_isi", "tts_quota_scope", fallback="")
            ).strip(),
            tts_campaign_id=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ID")
                or parser.get("aliyun_isi", "tts_campaign_id", fallback="")
            ).strip(),
            tts_campaign_starts_at=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_STARTS_AT")
                or parser.get("aliyun_isi", "tts_campaign_starts_at", fallback="")
            ).strip(),
            tts_campaign_ends_at=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ENDS_AT")
                or parser.get("aliyun_isi", "tts_campaign_ends_at", fallback="")
            ).strip(),
            tts_campaign_character_limit=int(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_CHARACTER_LIMIT")
                or parser.getint(
                    "aliyun_isi", "tts_campaign_character_limit", fallback=0
                )
            ),
            tts_provider_deadline_seconds=int(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_PROVIDER_DEADLINE_SECONDS")
                or parser.getint(
                    "aliyun_isi", "tts_provider_deadline_seconds", fallback=0
                )
            ),
            tts_price_cny_minor_per_10000_chars=int(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_PRICE_CNY_MINOR_PER_10000_CHARS")
                or parser.getint(
                    "aliyun_isi",
                    "tts_price_cny_minor_per_10000_chars",
                    fallback=0,
                )
            ),
            tts_pricing_revision=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_PRICING_REVISION")
                or parser.get("aliyun_isi", "tts_pricing_revision", fallback="")
            ).strip(),
            tts_usage_settlement_mode=(
                os.getenv("DORAMI_ALIYUN_ISI_TTS_USAGE_SETTLEMENT_MODE")
                or parser.get(
                    "aliyun_isi",
                    "tts_usage_settlement_mode",
                    fallback="manual",
                )
            ).strip(),
        ),
        llm=LLMConfig(
            base_url=(os.getenv("DORAMI_LLM_BASE_URL") or parser.get("llm", "base_url", fallback="")).strip(),
            api_key=(os.getenv("DORAMI_LLM_API_KEY") or parser.get("llm", "api_key", fallback="")).strip(),
            model=(os.getenv("DORAMI_LLM_MODEL") or parser.get("llm", "model", fallback="")).strip(),
            timeout_seconds=parser.getint("llm", "timeout_seconds", fallback=60),
            temperature=parser.getfloat("llm", "temperature", fallback=0.3),
            max_tokens=parser.getint("llm", "max_tokens", fallback=4096),
            map_concurrency=parser.getint("llm", "map_concurrency", fallback=4),
            thinking_mode=(os.getenv("DORAMI_LLM_THINKING_MODE") or parser.get("llm", "thinking_mode", fallback="")).strip(),
            aux_model=(os.getenv("DORAMI_LLM_AUX_MODEL") or parser.get("llm", "aux_model", fallback="")).strip(),
        ),
        x_api=XApiConfig(
            bearer_token=(
                os.getenv("DORAMI_X_BEARER_TOKEN")
                or parser.get("x_api", "bearer_token", fallback="")
            ).strip(),
            base_url=parser.get("x_api", "base_url", fallback="https://api.x.com/2").strip().rstrip("/"),
            timeout_seconds=parser.getint("x_api", "timeout_seconds", fallback=30),
            max_results=parser.getint("x_api", "max_results", fallback=25),
            monthly_budget_usd=parser.getfloat("x_api", "monthly_budget_usd", fallback=5.0),
        ),
    )


settings = load_config()
