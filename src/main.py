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

    # 启动 Uvicorn 服务器，指向 api.app 模块中的 app 实例。
    # reload 由配置驱动（开发热更新）；但生产环境（PM2 注入 NODE_ENV=production）
    # 一律强制关闭——reload 会另起文件监视子进程，徒增内存且不稳定，且与
    # 进程内调度/内存进度态相冲突。这是不依赖 ini 的兜底防御。
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
