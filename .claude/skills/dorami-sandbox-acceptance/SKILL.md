---
name: dorami-sandbox-acceptance
description: 为本仓某个 worktree 起隔离栈(临时 ini + 真实库副本 + uvicorn 8089 + vite 5174)并用 Playwright 自查截图、断言,再给用户可复制的目检步骤。用户说「起沙箱」「隔离栈」「目检一下」「验收」或波次实现完成需要自查时使用。
---

# 隔离栈验收

完整约束与选择器在 `docs/dev/sandbox-acceptance.md`。要点:

1. 临时 ini 用 sed 从主仓 `config/backend.ini` 派生:`database_url` 指沙箱 sqlite 绝对路径、`port=8089`(被占顺延)、`cookie_secure=false`、`[taxonomy] deployment=manual`、`[media] enabled` 按是否要看图片决定(要看封面 / 图片必须开并把 `media_dir` 指 scratchpad)。
2. 真实库副本:`sqlite3 data/cms_data.db ".backup '<scratch>/cms_data.db'"` → `DORAMI_CONFIG_FILE=<ini> .venv/bin/alembic upgrade heads`。
3. 起服:`DORAMI_CONFIG_FILE=<ini> .venv/bin/uvicorn --app-dir <worktree>/src api.app:app --port 8089`;`VITE_PROXY_TARGET=http://127.0.0.1:8089 npx vite --port 5174 --strictPort`。
4. 账号:空库自种 `admin/admin`;真实副本用 `services.accounts.create_user` 建 e2e 读者并 `complete_interest_onboarding`。要真模型评分时**先问用户授权**再搬密钥。
5. 播客 / 分析类数据按文档 §2 的约束播种或走接口订阅后抓取;AI 状态用 `ctx.route` 伪造。
6. Playwright:登录 → 目标页 → 亮 / 暗 / 移动壳截图 + 像素级断言(表头 36px、行高 46px、console error 为零);先自己目检截图再交用户。
7. 交付可直接复制执行的验收步骤;等用户在当前会话明确放行。
