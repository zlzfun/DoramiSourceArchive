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

    # 启动 Uvicorn 服务器，指向 api.app 模块中的 app 实例
    # reload=True 支持代码修改后热更新
    uvicorn.run(
        "api.app:app",
        host=settings.server.host,
        port=settings.server.port,
        reload=settings.server.reload,
    )
