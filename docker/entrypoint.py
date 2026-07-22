"""容器入口:与 src/main.py 同构,但监听地址按容器约定固定为 0.0.0.0:8088。

ini 里的 [server] host/port 在容器内不生效——容器必须监听 0.0.0.0 才能被
nginx 容器访问,对外端口由 compose 的端口映射决定;[nginx] 节同样不生效
(站点配置在 docker/nginx.conf)。其余配置(存储/认证/LLM/媒体库/X API 等)
照常读 DORAMI_CONFIG_FILE 指向的 ini。

与 main.py 一致的启动序:屏蔽网络警告 → 应用代理/镜像环境 → 数据库迁移
(ensure_migrated,失败即退出,不带漂移 schema 起服务)→ uvicorn(无 reload)。
"""
import warnings

import urllib3

from config import settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

settings.apply_process_environment()

from storage.migrations import ensure_migrated  # noqa: E402

print("🗂  正在核对数据库迁移(alembic upgrade head)...")
ensure_migrated(settings.storage.database_url)

import uvicorn  # noqa: E402

print("🚀 正在启动 AI CMS & RAG 后端 API 服务(容器内 0.0.0.0:8088)...")
uvicorn.run("api.app:app", host="0.0.0.0", port=8088)
