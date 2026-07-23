import os
import uvicorn
import warnings
import urllib3
from config import settings

# 1. 屏蔽各类网络相关的警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# 2. 全局网络与镜像环境配置
settings.apply_process_environment()

if __name__ == "__main__":
    print("=====================================================")
    print("🚀 正在启动 AI CMS & RAG 后端 API 服务...")
    print(f"👉 请在浏览器中打开 Web 调试面板: http://{settings.server.host}:{settings.server.port}/docs")
    print("=====================================================")

    # 3. 数据库迁移前置（与生产容器入口 docker/entrypoint.py 同一入口 ensure_migrated）：
    #    dev 裸起也先把文件库演进到 head，杜绝「运行期
    #    create_all 只建缺失表、schema 变更从未应用」的断面（内存库内部跳过；
    #    已在 head 时为幂等零操作）。迁移失败则让异常炸出来——带着漂移 schema
    #    起服务只会产生更难懂的运行时报错。
    from storage.migrations import ensure_migrated
    print("🗂  正在核对数据库迁移（alembic upgrade head）...")
    ensure_migrated(settings.storage.database_url)

    # 启动 Uvicorn 服务器，指向 api.app 模块中的 app 实例。
    # reload 由配置驱动（开发热更新）；NODE_ENV=production 时一律强制关闭——
    # reload 会另起文件监视子进程，徒增内存且不稳定，且与进程内调度/内存进度态
    # 相冲突。（生产已走 docker/entrypoint.py 不经此文件,此防御保护的是
    # 谁在生产机上手工裸起 main.py 的场景。）
    is_production = os.getenv("NODE_ENV") == "production"
    effective_reload = settings.server.reload and not is_production
    if is_production and settings.server.reload:
        print("⚠️ 检测到 NODE_ENV=production，已强制关闭 uvicorn reload。")

    uvicorn.run(
        "api.app:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=effective_reload,
    )
