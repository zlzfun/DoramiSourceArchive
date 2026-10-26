# 依赖管理(uv.lock 入库流程)

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Development Commands」注释原文迁出(2026-10-21)。
> 相关:[`testing.md`](./testing.md) · 守卫 `tests/test_uv_lock.py` / `tests/test_docker_requirements.py`

```bash
# Install dependencies (use uv, the project uses uv.lock)
uv sync    # dev 全量:uv sync --extra crawl4ai(浏览器详情后端)

# ⚠️ 改依赖(pyproject dependencies/extras)后的完整流程——uv.lock **入库**,改完必须提交
# 解析时避开开发机镜像源(否则锁里的 registry 会被改写成内网/区域镜像):
#   env -u UV_INDEX_URL -u UV_DEFAULT_INDEX uv lock && uv sync --extra crawl4ai
#   uv export --frozen --no-dev --no-hashes --no-emit-project -o docker/requirements.txt
#   uv export --frozen --no-dev --no-hashes --no-emit-project --extra crawl4ai -o docker/requirements-crawl4ai.txt
# 守卫:tests/test_uv_lock.py(锁 vs pyproject/version、无镜像源、无退役重型栈)
#       tests/test_docker_requirements.py(导出清单 vs pyproject;v3.17.0 生产曾因旧锁静默装回 torch)

# Run the backend server (starts on http://127.0.0.1:8088, hot-reload enabled)
```

补充(自自动记忆沉淀):本机 `uv run` / `uv sync` 会把 `uv.lock` 的 registry 改写成镜像源,**这份镜像改写永不提交**——**没改依赖**的 PR 提交前 `git checkout -- uv.lock` 把它还原;**改了依赖或开新开发版本(根包 version 行)时锁文件的合法变更必须提交**(按上面的 `env -u … uv lock` 流程重解析,守卫 `tests/test_uv_lock.py` 会拦镜像源);worktree 软链主仓 `.venv` 时别在 worktree 里跑带项目同步的 `uv run` / `uv sync`(它按 worktree 的锁改的是主仓 venv),一次性工具用 `uv run --no-project --with …`;`uv lock --offline` 会按本地缓存重解析(实测删掉一串包),别用它对齐版本号。
