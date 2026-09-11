"""外部第三方凭据统一保管层。

范围边界(勿误并):本层只管**我方持有、用于访问外部系统**的机密与其伴生
配置——LLM api_key、X API bearer_token、远程同步登录密码,以及未来的内网
SSO 工号密码、微信/微博/小红书/知乎等平台会话凭据。**本系统自签发的令牌**
(dsub_/dfeed_ 订阅令牌存 hash、dshr_ 分享令牌明文入库)各有拍板过的口径,
不属本层;账号体系的登录密码(PBKDF2,services/accounts.py)同样不属。

统一的是「保管与配置面契约」,不是「使用」:

- **存储**:AppSettingRecord KV 运行时覆盖(各命名空间沿用既有 key,零迁移),
  基线来自 env/ini——``config.settings`` 在进程启动时已按 env > ini 合并;
- **解析序**:runtime_kv > env > ini > default(``resolve_values`` /
  ``field_sources``,后者逐字段标注有效值来源,因为凭据可能来自环境变量、
  此时运行时值不生效);
- **只写不回显**:GET 只给 ``{field}_set`` 布尔 + 尾四位掩码预览
  (``sanitize_values`` / ``mask_tail``);POST 空 secret = 保留既有值
  (``save_updates``;JSON blob 形态用 ``apply_secret_update``);
- **日志/审计绝不落机密**:本层不打印任何字段值;admin_audit 的摘要注册表
  也不读密码字段。

刻意不进本层的东西:各域的连通性探针(LLM ping / X 最省钱探针 / remote-sync
登录探测)天然异质;字段取值校验(范围/格式)留在各 router;凭据的**使用**
(client 构造、请求签名)留在各域服务。

会话类凭据(cookie/设备指纹,会过期、要续期)未来接入时:凭据照常入本层
保管,获取/续期流程各平台特质化;健康度回写走普通(非 secret)字段约定
(如 last_verified_at / status),不需要新机制。

存储形态:主形态是**一字段一 KV key**(LLM/X API);remote_sync 定时凭据是
历史遗留的**单 JSON blob**(凭据与 cron/范围等非凭据配置混存),不强行迁移,
只复用本层的 secret 合并语义(``apply_secret_update``)。新命名空间一律用
主形态。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, MutableMapping, Tuple

from sqlmodel import Session

import config
from models.db import AppSettingRecord


# ==========================================
# 声明式命名空间
# ==========================================

@dataclass(frozen=True)
class CredentialField:
    """凭据命名空间里的一个字段。

    ``secret=True`` 的字段适用只写不回显契约;普通字段是凭据的伴生配置
    (base_url/timeout 等),一并纳入统一解析序。``ini_option`` 缺省与
    ``name`` 同名;``env_var`` 仅在该字段确有环境变量入口时填写。
    """

    name: str
    kv_key: str
    secret: bool = False
    kind: str = "str"  # str | int | float | bool | csv
    env_var: str = ""
    ini_option: str = ""

    @property
    def resolved_ini_option(self) -> str:
        return self.ini_option or self.name


@dataclass(frozen=True)
class CredentialNamespace:
    """一类外部凭据的声明:字段清单 + env/ini/KV 三层映射。"""

    name: str
    ini_section: str
    fields: Tuple[CredentialField, ...] = field(default_factory=tuple)
    clearable_secret_fields: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        known = {item.name: item for item in self.fields}
        for name in self.clearable_secret_fields:
            item = known.get(name)
            if item is None or not item.secret:
                raise ValueError(
                    "clearable credential fields must name declared secrets"
                )

    def field_by_name(self, name: str) -> CredentialField:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(f"credential namespace '{self.name}' has no field '{name}'")


LLM_NAMESPACE = CredentialNamespace(
    name="llm",
    ini_section="llm",
    fields=(
        CredentialField("base_url", "llm_base_url", env_var="DORAMI_LLM_BASE_URL"),
        CredentialField("api_key", "llm_api_key", secret=True, env_var="DORAMI_LLM_API_KEY"),
        CredentialField("model", "llm_model", env_var="DORAMI_LLM_MODEL"),
        CredentialField("temperature", "llm_temperature", kind="float"),
        CredentialField("max_tokens", "llm_max_tokens", kind="int"),
        CredentialField("thinking_mode", "llm_thinking_mode", env_var="DORAMI_LLM_THINKING_MODE"),
        # 辅助轻模型(同端点同 key 的第二模型名,轻量结构化调用用;空=不启用)
        CredentialField("aux_model", "llm_aux_model", env_var="DORAMI_LLM_AUX_MODEL"),
    ),
)

X_API_NAMESPACE = CredentialNamespace(
    name="x_api",
    ini_section="x_api",
    fields=(
        CredentialField("bearer_token", "x_api_bearer_token", secret=True, env_var="DORAMI_X_BEARER_TOKEN"),
        CredentialField("base_url", "x_api_base_url"),
        CredentialField("timeout_seconds", "x_api_timeout_seconds", kind="int"),
        CredentialField("max_results", "x_api_max_results", kind="int"),
        CredentialField("monthly_budget_usd", "x_api_monthly_budget_usd", kind="float"),
    ),
)

ALIYUN_ISI_NAMESPACE = CredentialNamespace(
    name="aliyun_isi",
    ini_section="aliyun_isi",
    fields=(
        CredentialField(
            "access_key_id",
            "aliyun_isi_access_key_id",
            secret=True,
            env_var="ALIYUN_AK_ID",
        ),
        CredentialField(
            "access_key_secret",
            "aliyun_isi_access_key_secret",
            secret=True,
            env_var="ALIYUN_AK_SECRET",
        ),
        CredentialField(
            "security_token",
            "aliyun_isi_security_token",
            secret=True,
            env_var="ALIYUN_SECURITY_TOKEN",
        ),
        CredentialField(
            "app_key",
            "aliyun_isi_app_key",
            secret=True,
            env_var="NLS_APP_KEY",
        ),
        CredentialField(
            "access_token",
            "aliyun_isi_access_token",
            secret=True,
            env_var="NLS_ACCESS_TOKEN",
        ),
        CredentialField(
            "token_expires_at",
            "aliyun_isi_token_expires_at",
            kind="int",
            env_var="NLS_TOKEN_EXPIRES_AT",
        ),
        CredentialField(
            "region_id",
            "aliyun_isi_region_id",
            env_var="DORAMI_ALIYUN_ISI_REGION_ID",
        ),
        CredentialField(
            "asr_domain",
            "aliyun_isi_asr_domain",
            env_var="DORAMI_ALIYUN_ISI_ASR_DOMAIN",
        ),
        CredentialField(
            "asr_product",
            "aliyun_isi_asr_product",
            env_var="DORAMI_ALIYUN_ISI_ASR_PRODUCT",
        ),
        CredentialField(
            "asr_api_version",
            "aliyun_isi_asr_api_version",
            env_var="DORAMI_ALIYUN_ISI_ASR_API_VERSION",
        ),
        CredentialField(
            "asr_task_version",
            "aliyun_isi_asr_task_version",
            env_var="DORAMI_ALIYUN_ISI_ASR_TASK_VERSION",
        ),
        CredentialField(
            "asr_enable_words",
            "aliyun_isi_asr_enable_words",
            kind="bool",
            env_var="DORAMI_ALIYUN_ISI_ASR_ENABLE_WORDS",
        ),
        CredentialField(
            "asr_auto_split",
            "aliyun_isi_asr_auto_split",
            kind="bool",
            env_var="DORAMI_ALIYUN_ISI_ASR_AUTO_SPLIT",
        ),
        CredentialField(
            "asr_enable_sample_rate_adaptive",
            "aliyun_isi_asr_enable_sample_rate_adaptive",
            kind="bool",
            env_var="DORAMI_ALIYUN_ISI_ASR_ENABLE_SAMPLE_RATE_ADAPTIVE",
        ),
        CredentialField(
            "token_url",
            "aliyun_isi_token_url",
            env_var="DORAMI_ALIYUN_ISI_TOKEN_URL",
        ),
        CredentialField(
            "tts_url",
            "aliyun_isi_tts_url",
            env_var="DORAMI_ALIYUN_ISI_TTS_URL",
        ),
        CredentialField(
            "tts_product",
            "aliyun_isi_tts_product",
            env_var="DORAMI_ALIYUN_ISI_TTS_PRODUCT",
        ),
        CredentialField(
            "tts_api_version",
            "aliyun_isi_tts_api_version",
            env_var="DORAMI_ALIYUN_ISI_TTS_API_VERSION",
        ),
        CredentialField(
            "tts_device_id",
            "aliyun_isi_tts_device_id",
            env_var="DORAMI_ALIYUN_ISI_TTS_DEVICE_ID",
        ),
        CredentialField(
            "tts_voice_profiles_json",
            "aliyun_isi_tts_voice_profiles_json",
            env_var="DORAMI_ALIYUN_ISI_TTS_VOICE_PROFILES_JSON",
        ),
        CredentialField(
            "tts_result_allowed_host_suffixes",
            "aliyun_isi_tts_result_allowed_host_suffixes",
            kind="csv",
            env_var="DORAMI_ALIYUN_ISI_TTS_RESULT_ALLOWED_HOST_SUFFIXES",
        ),
        CredentialField(
            "tts_max_chars",
            "aliyun_isi_tts_max_chars",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_TTS_MAX_CHARS",
        ),
        CredentialField(
            "request_timeout_seconds",
            "aliyun_isi_request_timeout_seconds",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_REQUEST_TIMEOUT_SECONDS",
        ),
        CredentialField(
            "asr_poll_interval_seconds",
            "aliyun_isi_asr_poll_interval_seconds",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_ASR_POLL_INTERVAL_SECONDS",
        ),
        CredentialField(
            "tts_poll_interval_seconds",
            "aliyun_isi_tts_poll_interval_seconds",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_TTS_POLL_INTERVAL_SECONDS",
        ),
        CredentialField(
            "token_refresh_skew_seconds",
            "aliyun_isi_token_refresh_skew_seconds",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_TOKEN_REFRESH_SKEW_SECONDS",
        ),
        CredentialField(
            "asr_quota_scope",
            "aliyun_isi_asr_quota_scope",
            env_var="DORAMI_ALIYUN_ISI_ASR_QUOTA_SCOPE",
        ),
        CredentialField(
            "asr_quota_timezone",
            "aliyun_isi_asr_quota_timezone",
            env_var="DORAMI_ALIYUN_ISI_ASR_QUOTA_TIMEZONE",
        ),
        CredentialField(
            "asr_daily_audio_seconds_limit",
            "aliyun_isi_asr_daily_audio_seconds_limit",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_ASR_DAILY_AUDIO_SECONDS_LIMIT",
        ),
        CredentialField(
            "asr_max_audio_seconds_per_file",
            "aliyun_isi_asr_max_audio_seconds_per_file",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_ASR_MAX_AUDIO_SECONDS_PER_FILE",
        ),
        CredentialField(
            "asr_entitlement_ends_at",
            "aliyun_isi_asr_entitlement_ends_at",
            env_var="DORAMI_ALIYUN_ISI_ASR_ENTITLEMENT_ENDS_AT",
        ),
        CredentialField(
            "asr_provider_deadline_seconds",
            "aliyun_isi_asr_provider_deadline_seconds",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_ASR_PROVIDER_DEADLINE_SECONDS",
        ),
        CredentialField(
            "asr_price_cny_minor_per_hour",
            "aliyun_isi_asr_price_cny_minor_per_hour",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_ASR_PRICE_CNY_MINOR_PER_HOUR",
        ),
        CredentialField(
            "asr_pricing_revision",
            "aliyun_isi_asr_pricing_revision",
            env_var="DORAMI_ALIYUN_ISI_ASR_PRICING_REVISION",
        ),
        CredentialField(
            "tts_quota_scope",
            "aliyun_isi_tts_quota_scope",
            env_var="DORAMI_ALIYUN_ISI_TTS_QUOTA_SCOPE",
        ),
        CredentialField(
            "tts_campaign_id",
            "aliyun_isi_tts_campaign_id",
            env_var="DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ID",
        ),
        CredentialField(
            "tts_campaign_starts_at",
            "aliyun_isi_tts_campaign_starts_at",
            env_var="DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_STARTS_AT",
        ),
        CredentialField(
            "tts_campaign_ends_at",
            "aliyun_isi_tts_campaign_ends_at",
            env_var="DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_ENDS_AT",
        ),
        CredentialField(
            "tts_campaign_character_limit",
            "aliyun_isi_tts_campaign_character_limit",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_TTS_CAMPAIGN_CHARACTER_LIMIT",
        ),
        CredentialField(
            "tts_provider_deadline_seconds",
            "aliyun_isi_tts_provider_deadline_seconds",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_TTS_PROVIDER_DEADLINE_SECONDS",
        ),
        CredentialField(
            "tts_price_cny_minor_per_10000_chars",
            "aliyun_isi_tts_price_cny_minor_per_10000_chars",
            kind="int",
            env_var="DORAMI_ALIYUN_ISI_TTS_PRICE_CNY_MINOR_PER_10000_CHARS",
        ),
        CredentialField(
            "tts_pricing_revision",
            "aliyun_isi_tts_pricing_revision",
            env_var="DORAMI_ALIYUN_ISI_TTS_PRICING_REVISION",
        ),
        CredentialField(
            "tts_usage_settlement_mode",
            "aliyun_isi_tts_usage_settlement_mode",
            env_var="DORAMI_ALIYUN_ISI_TTS_USAGE_SETTLEMENT_MODE",
        ),
    ),
)

# 新的外部凭据(内网 SSO、微信/微博等)在此登记命名空间即可获得整套契约。
REGISTRY: Dict[str, CredentialNamespace] = {
    ns.name: ns
    for ns in (
        LLM_NAMESPACE,
        X_API_NAMESPACE,
        ALIYUN_ISI_NAMESPACE,
    )
}


# ==========================================
# KV 读写(AppSettingRecord)
# ==========================================

def get_setting(session: Session, key: str) -> str:
    record = session.get(AppSettingRecord, key)
    return str(record.value or "") if record is not None else ""


def _stage_setting(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)


def set_setting(session: Session, key: str, value: str) -> None:
    _stage_setting(session, key, value)
    session.commit()


# ==========================================
# 契约四能力:resolve / sources / save / sanitize
# ==========================================

def _coerce(raw: str, kind: str, fallback: Any) -> Any:
    if kind == "int":
        try:
            return int(raw)
        except ValueError:
            return fallback
    if kind == "float":
        try:
            return float(raw)
        except ValueError:
            return fallback
    if kind == "bool":
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return fallback
    if kind == "csv":
        return tuple(item.strip() for item in raw.split(",") if item.strip())
    return raw


def resolve_values(session: Session, ns: CredentialNamespace, baseline: Any) -> Dict[str, Any]:
    """合并 env/ini 基线与 KV 运行时覆盖,产出各字段有效值。

    ``baseline`` 是启动时已合并 env > ini 的默认对象(``config.settings.<ns>``);
    KV 值为空串视同未覆盖,数值字段解析失败回落基线。
    """
    values: Dict[str, Any] = {}
    for f in ns.fields:
        raw = get_setting(session, f.kv_key).strip()
        fallback = getattr(baseline, f.name)
        values[f.name] = _coerce(raw, f.kind, fallback) if raw else fallback
    return values


def field_sources(session: Session, ns: CredentialNamespace) -> Dict[str, str]:
    """逐字段标注有效值来源:runtime_kv | env | ini | default。"""
    parser = config._read_config_file()  # 与 load_config 使用同一路径裁决
    sources: Dict[str, str] = {}
    for f in ns.fields:
        if get_setting(session, f.kv_key).strip():
            sources[f.name] = "runtime_kv"
        elif f.env_var and os.getenv(f.env_var, "").strip():
            sources[f.name] = "env"
        elif parser.has_option(ns.ini_section, f.resolved_ini_option):
            sources[f.name] = "ini"
        else:
            sources[f.name] = "default"
    return sources


def overall_source(sources: Mapping[str, str]) -> str:
    """按优先级概括当前配置来源,详情仍以 field_sources 为准。"""
    for source in ("runtime_kv", "env", "ini", "default"):
        if source in sources.values():
            return source
    return "default"


def save_updates(session: Session, ns: CredentialNamespace, updates: Mapping[str, Any]) -> None:
    """写运行时覆盖。值应已经过调用方(router)校验/归一。

    ``None`` = 不修改;secret 字段空串同样 = 保留既有值(只写不回显契约的
    写入半边);普通字段允许写空串——即清除 KV 覆盖、回落 env/ini 基线。
    同一命名空间的一次更新只提交一个事务；全是 no-op 值时不触碰调用方事务。
    """
    staged = False
    try:
        for f in ns.fields:
            if f.name not in updates:
                continue
            value = updates[f.name]
            if value is None:
                continue
            if f.kind == "csv" and not isinstance(value, str):
                if not isinstance(value, (list, tuple)) or any(
                    not isinstance(item, str) for item in value
                ):
                    raise ValueError(
                        f"{f.name} must be a CSV string or string list"
                    )
                text = ",".join(item.strip() for item in value if item.strip())
            else:
                text = str(value).strip()
            if f.secret and not text:
                continue
            _stage_setting(session, f.kv_key, text)
            staged = True
        if staged:
            session.commit()
    except Exception:
        session.rollback()
        raise


def clear_secret_fields(
    session: Session,
    ns: CredentialNamespace,
    field_names: Tuple[str, ...],
) -> None:
    """Clear only namespace-declared ephemeral secrets from runtime KV.

    Ordinary secrets deliberately retain the established empty-value-means-keep
    contract. Callers cannot supply KV keys, and a namespace must explicitly
    opt each field into this destructive operation.
    """

    requested = tuple(dict.fromkeys(str(name or "").strip() for name in field_names))
    if not requested or any(not name for name in requested):
        raise ValueError("at least one clearable secret field is required")
    if not set(requested).issubset(ns.clearable_secret_fields):
        raise ValueError("credential secret field is not clearable")
    try:
        for name in requested:
            credential_field = ns.field_by_name(name)
            _stage_setting(session, credential_field.kv_key, "")
        session.commit()
    except Exception:
        session.rollback()
        raise


def mask_tail(value: str, keep: int = 4) -> str:
    """secret 的展示预览:只露尾部 keep 位;短值全遮;空值给空串。"""
    text = (value or "").strip()
    if not text:
        return ""
    if len(text) <= keep:
        return "*" * len(text)
    return f"••••{text[-keep:]}"


def sanitize_values(ns: CredentialNamespace, values: Mapping[str, Any]) -> Dict[str, Any]:
    """把有效值转成可回传的响应形状:secret → ``{name}_set`` + ``{name}_preview``。"""
    out: Dict[str, Any] = {}
    for f in ns.fields:
        value = values.get(f.name)
        if f.secret:
            text = str(value or "")
            out[f"{f.name}_set"] = bool(text)
            out[f"{f.name}_preview"] = mask_tail(text)
        else:
            out[f.name] = value
    return out


# ==========================================
# JSON blob 形态的共享语义(remote_sync 定时凭据)
# ==========================================

def apply_secret_update(target: MutableMapping[str, Any], updates: Mapping[str, Any], key: str) -> None:
    """blob 内 secret 的合并写:空/缺失 = 保留 ``target`` 里既有值。"""
    value = updates.get(key)
    if value:
        target[key] = str(value)
