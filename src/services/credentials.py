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
    kind: str = "str"  # str | int | float
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

# 新的外部凭据(内网 SSO、微信/微博等)在此登记命名空间即可获得整套契约。
REGISTRY: Dict[str, CredentialNamespace] = {
    ns.name: ns for ns in (LLM_NAMESPACE, X_API_NAMESPACE)
}


# ==========================================
# KV 读写(AppSettingRecord)
# ==========================================

def get_setting(session: Session, key: str) -> str:
    record = session.get(AppSettingRecord, key)
    return str(record.value or "") if record is not None else ""


def set_setting(session: Session, key: str, value: str) -> None:
    record = session.get(AppSettingRecord, key)
    if record is None:
        record = AppSettingRecord(key=key, value=value)
    else:
        record.value = value
    session.add(record)
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
    """
    for f in ns.fields:
        if f.name not in updates:
            continue
        value = updates[f.name]
        if value is None:
            continue
        text = str(value).strip()
        if f.secret and not text:
            continue
        set_setting(session, f.kv_key, text)


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
