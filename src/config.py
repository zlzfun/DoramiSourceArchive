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
    enabled: bool = False


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
    cookie_name: str = "dorami_admin_session"
    session_seconds: int = 604800
    admin_users: list["AuthCredential"] = None
    user_users: list["AuthCredential"] = None
    secret: Optional[str] = None
    cookie_secure: bool = False

    @property
    def username(self) -> str:
        return self.admin_users[0].username if self.admin_users else "admin"

    @property
    def password(self) -> str:
        return self.admin_users[0].password if self.admin_users else "admin"

    def __post_init__(self):
        if self.admin_users is None:
            object.__setattr__(self, "admin_users", [AuthCredential("admin", "admin")])
        if self.user_users is None:
            object.__setattr__(self, "user_users", [])


@dataclass(frozen=True)
class AuthCredential:
    username: str
    password: str


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
class WechatConfig:
    auth_base_dir: str


@dataclass(frozen=True)
class XiaolubanConfig:
    url: str = "http://xiaoluban.rnd.huawei.com:80/"
    auth: str = ""
    receiver: str = ""


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
class ImageHostConfig:
    upload_url_template: str = (
        "http://3ms.huawei.com/hi/restnew/editor/attach/upload"
        "?app_id=67&public_key=10067&current_timestamp={timestamp}&verify_code={verify_code}"
    )
    secret_key: str = ""
    timeout_seconds: int = 15


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
    wechat: WechatConfig
    xiaoluban: XiaolubanConfig
    image_host: ImageHostConfig
    llm: LLMConfig

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
    return [
        PROJECT_ROOT / "config" / "backend.ini",
        PROJECT_ROOT / "config" / "local.ini",
    ]


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


def _auth_credentials(raw_value: str) -> list[AuthCredential]:
    credentials: list[AuthCredential] = []
    for item in _csv(raw_value):
        if ":" not in item:
            raise ValueError("Auth whitelist entries must use 'username:password' format")
        username, password = item.split(":", 1)
        username = username.strip()
        if not username:
            raise ValueError("Auth whitelist username cannot be empty")
        if not password:
            raise ValueError("Auth whitelist password cannot be empty")
        credentials.append(AuthCredential(username=username, password=password))
    return credentials


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
    legacy_auth_user = parser.get("auth", "username", fallback="admin")
    legacy_auth_password = parser.get("auth", "password", fallback="admin")
    admin_users = _auth_credentials(
        parser.get("auth", "admin_users", fallback=f"{legacy_auth_user}:{legacy_auth_password}")
    )
    user_users = _auth_credentials(parser.get("auth", "user_users", fallback=""))

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
            admin_users=admin_users,
            user_users=user_users,
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
        wechat=WechatConfig(
            auth_base_dir=_path(parser.get("wechat", "auth_base_dir", fallback=".wechat_auth")),
        ),
        xiaoluban=XiaolubanConfig(
            url=parser.get("xiaoluban", "url", fallback="http://xiaoluban.rnd.huawei.com:80/"),
            auth=parser.get("xiaoluban", "auth", fallback=""),
            receiver=parser.get("xiaoluban", "receiver", fallback=""),
        ),
        image_host=ImageHostConfig(
            upload_url_template=parser.get(
                "image_host",
                "upload_url_template",
                fallback=ImageHostConfig.upload_url_template,
            ),
            secret_key=parser.get("image_host", "secret_key", fallback=""),
            timeout_seconds=parser.getint("image_host", "timeout_seconds", fallback=15),
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
    )


settings = load_config()
