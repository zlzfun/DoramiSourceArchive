@AGENTS.md

# Claude Code 专属附注

- 与 Codex 共用的全部须知在 `AGENTS.md`(上面已导入,以它为准);本文只放 Claude Code 独有的事。
- **路径作用域规则** `.claude/rules/`:`frontend.md`(`frontend/**`)、`migrations.md`(`src/models/**` `alembic/**` `src/storage/**`)、`deploy-scripts.md`(部署脚本 / `docker/` / workflows)、`tests.md`(`tests/**`)。触到匹配文件时自动加载;它们只是硬规则 + 指针,正文仍在 `docs/`。
- **项目技能** `.claude/skills/`:`dorami-issue-loop`(一条 issue 从开工到合入的闭环)、`dorami-sandbox-acceptance`(隔离栈 Playwright 验收)、`dorami-wave-docs`(文档同波 / 归档 / 决策行 / 守卫)。
- **自动记忆**(`~/.claude/projects/…/memory/`)只放:用户偏好与纠正、本机环境、仓外项目、私密事项。事实与规则进仓、可复用流程进技能;凡 `AGENTS.md` / `docs/` 已说的不写记忆。生产主机与运维实录在个人技能(`~/.claude/skills/dorami-prod-ops`),**不入库**。
- **Write / Edit 工具会把源码里的 `\uXXXX` 转义解码成真字符**(正则里的零宽空格、全角空格等会变成不可见字符,肉眼看不出):带转义的行用 Bash 跑 Python 脚本写入(以 `chr(92)` 拼反斜杠),写完用 Python 扫一遍非 ASCII 字符。
- 发版、部署、生产库只读统计等高影响操作先问用户;生产数据拷回本机须由用户本人执行(权限分类器会拦)。
