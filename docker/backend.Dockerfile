# 后端运行时镜像:Python 3.12 + 项目依赖(CPU 版 torch)+ Playwright Chromium。
# 镜像内 OS 固定为 Debian bookworm,deploy.sh 里「宿主 OS 过新导致 Playwright
# 自带浏览器装不上」的三层兜底自此不再需要。
FROM python:3.12-slim-bookworm

# tzdata:APScheduler 的 cron 按进程本地时区解释,compose 里 TZ=Asia/Shanghai
# 需要它才生效;curl 供容器健康检查使用。
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/

WORKDIR /app

# 依赖层单独成层:只 COPY 清单文件,改源码不触发依赖重装。
# 版本以 uv.lock 为准(--frozen:锁文件与 pyproject 不一致时构建失败,而非静默重解析)。
# torch 瘦身两步走(锁文件在 linux 平台钉的是 CUDA 版 torch + 约 5GB 的
# cuda-toolkit/nvidia-*/triton 伴生包,本项目嵌入模型只跑 CPU):
#   1) grep 把 CUDA 伴生包从安装清单里滤掉;
#   2) UV_TORCH_BACKEND=cpu 把 torch 本体改道 PyTorch 官方 CPU 轮子索引
#      (同版本号的 +cpu 构建,不依赖任何 nvidia 包)。
# 若所用 uv 版本不识别该变量,镜像会退化为装上 PyPI 的 CUDA 版 torch——能跑但巨肥,
# 届时按 https://docs.astral.sh/uv/guides/integration/pytorch/ 换显式 CPU 索引方案。
ARG PIP_INDEX=https://pypi.org/simple
ENV UV_DEFAULT_INDEX=${PIP_INDEX}
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/requirements.txt \
 && grep -vE '^(nvidia-|cuda-|triton==)' /tmp/requirements.txt > /tmp/requirements.cpu.txt \
 && UV_TORCH_BACKEND=cpu uv pip install --system -r /tmp/requirements.cpu.txt \
 && rm /tmp/requirements*.txt

# Playwright Chromium + 其系统依赖(rss_openai_news 节点的 Cloudflare 渲染路径)。
RUN playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
COPY docker/entrypoint.py ./docker/entrypoint.py

# PYTHONPATH 与 ecosystem.config.js 同语义;WORKDIR=/app 使 ini 里的相对路径
# (data/cms_data.db、data/chroma_db、data/media)都落在挂载卷 /app/data 下。
ENV PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1

EXPOSE 8088
CMD ["python", "docker/entrypoint.py"]
