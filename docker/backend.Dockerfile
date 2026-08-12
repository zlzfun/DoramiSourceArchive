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

# 依赖层单独成层:只 COPY 钉版清单,改源码不触发依赖重装。
# 版本事实来源是入库的 docker/requirements*.txt(由 `uv export` 从 uv.lock 生成:
# 纯 name==version 行、零 URL,与开发机的镜像源改写解耦——uv.lock 本身按惯例不入库,
# v3.17.0 生产曾因“旧锁 + --frozen 不校验”静默装回 torch,故改此方案)。
# 改依赖后必须重导出并提交该清单(见 CLAUDE.md),tests/test_docker_requirements.py 兜底。
ARG PIP_INDEX=https://pypi.org/simple
ENV UV_DEFAULT_INDEX=${PIP_INDEX}
COPY docker/requirements.txt /tmp/
RUN uv pip install --system -r /tmp/requirements.txt \
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
