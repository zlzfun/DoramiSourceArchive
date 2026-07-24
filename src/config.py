import configparser
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
class RagConfig:
    """RAG 子系统开关与部署形态。

    三个 URL 决定形态(v3.17 服务化):
    - 全空 = **嵌入模式**(chromadb PersistentClient + 进程内 sentence-transformers,
      需装 rag-embedded extra / WITH_RAG=1 镜像)——dev 与单机全量形态;
    - chroma_url + embedding_url 有值 = **远程模式**(chroma server + TEI 容器,
      compose --profile rag;后端保持瘦身镜像);
    - rerank_url 独立可选:空则远程模式跳过重排(嵌入模式回落本地 CrossEncoder)。
    """

    enabled: bool = False
    chroma_url: str = ""
    embedding_url: str = ""
    rerank_url: str = ""

    @property
    def remote(self) -> bool:
        return bool(self.chroma_url)


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
    chroma_path: str


@dataclass(frozen=True)
class ModelConfig:
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"


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

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.api_key and self.model)


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
class AppConfig:
    server: ServerConfig
    runtime: RuntimeConfig
    rag: RagConfig
    network: NetworkConfig
    proxy: ProxyConfig
    auth: AuthConfig
    storage: StorageConfig
    models: ModelConfig
    cors: CorsConfig
    llm: LLMConfig
    x_api: XApiConfig
    media: MediaConfig

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


def load_config() -> AppConfig:
    parser = _read_config_file()

    storage_db = f"sqlite:///{PROJECT_ROOT / 'data' / 'cms_data.db'}"
    storage_chroma = str(PROJECT_ROOT / "data" / "chroma_db")
    runtime_role = os.getenv("DORAMI_RUNTIME_ROLE") or parser.get("runtime", "role", fallback="all")
    rag_enabled_raw = os.getenv("DORAMI_RAG_ENABLED")
    if rag_enabled_raw is None:
        rag_enabled = parser.getboolean("rag", "enabled", fallback=False)
    else:
        rag_enabled = rag_enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
    media_enabled_raw = os.getenv("DORAMI_MEDIA_ENABLED")
    if media_enabled_raw is None:
        media_enabled = parser.getboolean("media", "enabled", fallback=True)
    else:
        media_enabled = media_enabled_raw.strip().lower() in {"1", "true", "yes", "on"}
    return AppConfig(
        server=ServerConfig(
            host=parser.get("server", "host", fallback="127.0.0.1"),
            port=parser.getint("server", "port", fallback=8088),
            reload=parser.getboolean("server", "reload", fallback=True),
        ),
        runtime=RuntimeConfig(
            role=_runtime_role(runtime_role),
        ),
        rag=RagConfig(
            enabled=rag_enabled,
            chroma_url=(os.getenv("DORAMI_RAG_CHROMA_URL") or parser.get("rag", "chroma_url", fallback="")).strip().rstrip("/"),
            embedding_url=(os.getenv("DORAMI_RAG_EMBEDDING_URL") or parser.get("rag", "embedding_url", fallback="")).strip().rstrip("/"),
            rerank_url=(os.getenv("DORAMI_RAG_RERANK_URL") or parser.get("rag", "rerank_url", fallback="")).strip().rstrip("/"),
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
            chroma_path=_path(parser.get("storage", "chroma_path", fallback=storage_chroma)),
        ),
        models=ModelConfig(
            embedding_model=parser.get("models", "embedding_model", fallback="BAAI/bge-m3"),
            reranker_model=parser.get("models", "reranker_model", fallback="BAAI/bge-reranker-v2-m3"),
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
        llm=LLMConfig(
            base_url=(os.getenv("DORAMI_LLM_BASE_URL") or parser.get("llm", "base_url", fallback="")).strip(),
            api_key=(os.getenv("DORAMI_LLM_API_KEY") or parser.get("llm", "api_key", fallback="")).strip(),
            model=(os.getenv("DORAMI_LLM_MODEL") or parser.get("llm", "model", fallback="")).strip(),
            timeout_seconds=parser.getint("llm", "timeout_seconds", fallback=60),
            temperature=parser.getfloat("llm", "temperature", fallback=0.3),
            max_tokens=parser.getint("llm", "max_tokens", fallback=4096),
            map_concurrency=parser.getint("llm", "map_concurrency", fallback=4),
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
