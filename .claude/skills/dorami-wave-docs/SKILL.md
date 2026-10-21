---
name: dorami-wave-docs
description: 本仓「文档同波」收口:version-history 年表 + 详细段、backlog、decisions 行、方案文档归档(git mv 到 docs/archive 并加归档注)、索引更新、跑 scripts/check_docs.py 守卫。波次收口、合入前、用户说「文档同波」「归档方案」「补文档」时使用。
---

# 文档同波与归档

规则全文:`docs/dev/workflow.md` §4;索引规范在 `docs/README.md` 头部。

1. `docs/version-history.md`:「年表」节加一行(`- **<波名>(issue #N,状态)**:现状是什么 / 落在哪`);文末或对应位置加一段详细记录(设计取舍、目检返修、检视返修、自测结论、实现方 / 检视方)。
2. `docs/backlog.md`:更新进行中条目;有意不做的遗留与观察期写清;完结的移到「已完结」只留指针。
3. `docs/decisions.md`:为本波加 1–5 行——`- 决策 / 不做 / 触发:<一句> — <出处链接>`,放在对应主题节下;被取代的旧行不删,末尾加「→ 已被 … 取代」。
4. 方案落地后:`git mv docs/plans/issue-N-<slug>.md docs/archive/`,文首加 `> **归档于 YYYY-MM-DD(issue #N,vX.Y.Z)**:现状以 docs/architecture/ 与代码为准,文中路径与状态为当时记录。`;在 `docs/archive/README.md` 对应主题下加一行;从 `docs/README.md` 活跃方案区删掉。
5. 立了新规则 / 改了不变量:更新 `docs/frontend/conventions.md` 或 `docs/architecture/<域>.md` 对应节;**只有不变量或命令变化才改 `AGENTS.md`**。
6. 新方案文件名 `docs/plans/issue-N-<slug>.md`,首段 `> 状态:进行中 | 排队中 | 已落地 vX.Y.Z | 耐久参考 | 归档`。
7. 跑 `python3 scripts/check_docs.py`(索引覆盖 / 断链 / 状态词表 / L0 体量 / 文件名)直到零错误。
