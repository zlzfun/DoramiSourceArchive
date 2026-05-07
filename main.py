import uvicorn
import os
import warnings
import urllib3

# 1. 屏蔽各类网络相关的警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore")

# 2. 全局网络与镜像环境配置
os.environ['CURL_CA_BUNDLE'] = ''
os.environ['REQUESTS_CA_BUNDLE'] = ''
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

if __name__ == "__main__":
    print("=====================================================")
    print("🚀 正在启动 AI CMS & RAG 后端 API 服务...")
    print("👉 请在浏览器中打开 Web 调试面板: http://127.0.0.1:8088/docs")
    print("=====================================================")

    # 启动 Uvicorn 服务器，指向 api.app 模块中的 app 实例
    # reload=True 支持代码修改后热更新
    uvicorn.run("api.app:app", host="127.0.0.1", port=8088, reload=True)
