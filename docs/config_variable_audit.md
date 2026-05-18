# 配置变量审计

审计分支：`codex/config-variable-audit`

更新：已将非业务目录类的部署配置、敏感配置和前端运行配置收敛到配置文件读取。后端示例见 `config/backend.example.ini`，前端配置见 `frontend/app.config.json`。

审计范围：`src/`、`frontend/src/`、`frontend/vite.config.js`、`tests/`。未把 `uv.lock`、`frontend/package-lock.json`、历史设计文档中的示例文本计入统计。

## 统计口径

- 环境变量读取/写入：直接调用 `os.getenv`、`os.environ.get`、`os.environ.setdefault`、`os.environ[...]`、`globalThis.process?.env` 的位置。
- 硬编码配置：服务启动参数、认证默认值、路径、模型名、数据源目录、默认阈值、前端 API 路径、代理目标等会影响部署或运行行为的字面值。
- 未计入：Pydantic/SQLModel 字段的空字符串默认值、业务数据结构字段默认值、测试样例数据、普通 UI 文案。

## 总览

| 类型 | 数量 | 说明 |
| --- | ---: | --- |
| 环境变量读/写调用点 | 16 | 涉及 13 个唯一环境变量名 |
| 明显的运行时/部署硬编码配置组 | 23 | 服务、认证、存储、向量模型、前端、测试脚本等 |
| 内置数据源目录硬编码属性 | 356 | RSS、GitHub Release、网页、微信公众号 fetcher 的 class 配置属性 |

## 环境变量读取/写入清单

| 文件 | 行 | 变量/配置 | 环境变量名 | 默认值或行为 |
| --- | ---: | --- | --- | --- |
| `src/main.py` | 11 | `os.environ['CURL_CA_BUNDLE']` | `CURL_CA_BUNDLE` | 写死为空字符串 |
| `src/main.py` | 12 | `os.environ['REQUESTS_CA_BUNDLE']` | `REQUESTS_CA_BUNDLE` | 写死为空字符串 |
| `src/main.py` | 13 | `os.environ['HF_ENDPOINT']` | `HF_ENDPOINT` | 写死为 `https://hf-mirror.com` |
| `src/api/app.py` | 125 | `AUTH_SESSION_SECONDS` | `DORAMI_SESSION_SECONDS` | 默认 `604800` |
| `src/api/app.py` | 126 | `AUTH_USERNAME` | `DORAMI_ADMIN_USERNAME` | 默认 `admin` |
| `src/api/app.py` | 127 | `AUTH_PASSWORD` | `DORAMI_ADMIN_PASSWORD` | 默认 `admin` |
| `src/api/app.py` | 128 | `AUTH_SECRET` | `DORAMI_AUTH_SECRET` | 未设置时由密码和路径派生 |
| `src/api/app.py` | 137 | `_auth_cookie_secure()` | `DORAMI_AUTH_COOKIE_SECURE` | 默认关闭 |
| `src/storage/impl/vector_storage.py` | 175 | `model_name_or_path` | `LOCAL_MODEL_PATH` | 默认 `BAAI/bge-m3` |
| `src/storage/impl/vector_storage.py` | 195 | `self._reranker_model` | `RERANKER_MODEL_PATH` | 默认 `BAAI/bge-reranker-v2-m3` |
| `src/fetchers/impl/wechat_gzh_fetcher.py` | 40 | `auth` | `XIAOLUBAN_AUTH` | 未设置则跳过通知 |
| `src/fetchers/impl/wechat_gzh_fetcher.py` | 41 | `receiver` | `XIAOLUBAN_RECEIVER` | 未设置则跳过通知 |
| `tests/rag/evaluate.py` | 37 | `os.environ.setdefault('HF_ENDPOINT')` | `HF_ENDPOINT` | 默认 `https://hf-mirror.com` |
| `tests/rag/evaluate.py` | 264 | `model_name` | `LOCAL_MODEL_PATH` | 默认 `BAAI/bge-m3` |
| `tests/rag/evaluate.py` | 266 | `reranker_name` | `RERANKER_MODEL_PATH` | 默认 `BAAI/bge-reranker-v2-m3` |
| `frontend/vite.config.js` | 11 | dev proxy target | `VITE_API_PROXY_TARGET` | 默认 `http://127.0.0.1:8088` |

唯一环境变量名：`CURL_CA_BUNDLE`、`REQUESTS_CA_BUNDLE`、`HF_ENDPOINT`、`DORAMI_SESSION_SECONDS`、`DORAMI_ADMIN_USERNAME`、`DORAMI_ADMIN_PASSWORD`、`DORAMI_AUTH_SECRET`、`DORAMI_AUTH_COOKIE_SECURE`、`LOCAL_MODEL_PATH`、`RERANKER_MODEL_PATH`、`XIAOLUBAN_AUTH`、`XIAOLUBAN_RECEIVER`、`VITE_API_PROXY_TARGET`。

## 硬编码运行时配置清单

| 文件 | 行 | 配置 | 当前硬编码 |
| --- | ---: | --- | --- |
| `src/main.py` | 11-13 | CA bundle 与 Hugging Face 镜像 | 直接覆盖进程环境变量 |
| `src/main.py` | 23 | Uvicorn 启动参数 | `host='127.0.0.1'`、`port=8088`、`reload=True` |
| `src/api/app.py` | 101 | FastAPI 标题 | `Dorami 数据归档中枢 API` |
| `src/api/app.py` | 104-109 | CORS 策略 | `allow_origins=['*']` 等 |
| `src/api/app.py` | 113 | SQLite 数据库路径 | `data/cms_data.db` |
| `src/api/app.py` | 114 | ChromaDB 路径 | `data/chroma_db` |
| `src/api/app.py` | 117 | MCP 挂载路径 | `/mcp` |
| `src/api/app.py` | 124-128 | 管理员认证默认值 | cookie 名、用户名、密码、会话秒数、secret fallback |
| `src/api/app.py` | 1931-1939 | RAG 查询默认值 | `top_k=5`、`max_chars=4000`、`score_threshold=1.5` 等 |
| `src/storage/impl/vector_storage.py` | 20-37 | 文本清洗阈值/正则 | `_MIN_BODY_CHARS=30` 等 |
| `src/storage/impl/vector_storage.py` | 75-92 | 来源展示名映射 | `SOURCE_FRIENDLY_NAMES` 字典 |
| `src/storage/impl/vector_storage.py` | 109 | chunk 参数 | `chunk_size=800`、`overlap=150` |
| `src/storage/impl/vector_storage.py` | 166 | Chroma 默认参数 | `db_path='./data/chroma_db'`、`collection_name='dorami_docs'` |
| `src/storage/impl/vector_storage.py` | 174-195 | 向量模型默认值 | `BAAI/bge-m3`、`BAAI/bge-reranker-v2-m3` |
| `src/fetchers/base.py` | 42-49 | 抓取通用参数 | `timeout=30`、`max_retries=3`、User-Agent |
| `src/fetchers/impl/wechat_gzh_fetcher.py` | 24 | 微信认证文件目录 | 当前工作目录下 `.wechat_auth` |
| `src/fetchers/impl/wechat_gzh_fetcher.py` | 49-88 | 内网通知/图床上传 | 小鲁班 URL、3ms 上传 URL、`secret_key`、timeout |
| `frontend/src/api.js` | 1 | 前端 API 根路径 | `/api` |
| `frontend/src/App.jsx` | 21 | logo 路径 | `/logo.png` |
| `frontend/src/components/LoginScreen.jsx` | 4 | logo 路径 | `/logo.png` |
| `frontend/vite.config.js` | 8-11 | Vite dev server | `port=5173`、`/api` proxy、默认后端地址 |
| `tests/rag/evaluate.py` | 30-37 | 测试路径与镜像 | `src`、`data`、`results`、`HF_ENDPOINT` |
| `tests/rag/evaluate.py` | 268-274 | RAG 评估存储默认值 | collection `dorami_docs`、`data/chroma_db`、`data/cms_data.db` |

## 内置数据源目录统计

这些配置是产品内置的数据源目录。它们不是密钥，但属于可变业务配置，继续硬编码会让新增/禁用/修改数据源必须发版。

| 文件 | class 数 | 硬编码属性数 | 主要属性 |
| --- | ---: | ---: | --- |
| `src/fetchers/impl/rss_fetcher.py` | 24 | 148 | `source_id`、`name`、`description`、`icon`、`feed_url`、`category`、`default_limit` |
| `src/fetchers/impl/github_release_fetcher.py` | 14 | 84 | `source_id`、`name`、`description`、`icon`、`owner`、`repo`、默认 release 参数 |
| `src/fetchers/impl/webpage_fetcher.py` | 7 | 66 | `listing_url`、`site_name`、`source_section`、URL include/exclude patterns、默认抓正文参数 |
| `src/fetchers/impl/wechat_gzh_fetcher.py` | 10 | 58 | `source_id`、`target_account`、`name`、`description`、`icon`、`category` |

## 判断与建议

硬编码应当拆成三类处理：

1. 密钥、账号、密码、token、内部服务地址：优先环境变量或密钥管理系统。当前 `DORAMI_ADMIN_PASSWORD` 默认 `admin`、`ImageHostUploader.secret_key`、内网 URL 都应优先治理。
2. 部署环境差异配置：适合环境变量加集中 Settings 类，例如 host、port、database URL、Chroma path、CORS origins、HF endpoint、Vite proxy target。
3. 业务目录和产品默认值：适合配置文件或数据库，例如 RSS feed 列表、GitHub repo 列表、网页抓取规则、微信公众号账号、RAG 默认 `top_k/max_chars`、chunk size、source friendly names。

建议路线：

- 新增一个统一配置入口，例如 `src/config.py`，用 `pydantic-settings` 读取环境变量，并集中声明默认值、类型转换和校验。
- 新增一份非敏感配置文件，例如 `config/sources.yaml` 或 `config/sources.toml`，承载数据源目录和默认抓取参数。敏感字段只存环境变量名引用，不把值写入文件。
- 保留代码内“安全默认值”只用于本地开发，并让生产环境缺少关键密钥时启动失败。管理员密码和 auth secret 不应有生产可用默认值。
- 对当前已有的 `SourceConfigRecord` 做利用：内置数据源可以从配置文件 seed 到数据库，运行时以数据库为准，代码只保留通用 fetcher 能力。
- 前端构建期配置用 `VITE_*` 环境变量，运行时 API base 更推荐由后端同源代理或 `public/config.json` 注入，避免重新构建才能改后端地址。
