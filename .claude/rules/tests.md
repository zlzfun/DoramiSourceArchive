---
paths:
  - "tests/**"
  - "frontend/test/**"
  - "scripts/check_*.py"
---

# 测试硬规则(正文:docs/dev/testing.md)

- 造数据用相对时间(`now-1d` / `now-45d`),滚动窗 + 固定日期 = 时间炸弹;CI 忽然红先在 main 跑同一用例。
- 涉及 `ensure_public_host` / `_resolve_host_ips` / SSRF 守卫的用例一律打桩(开发机 fake-ip 代理让占位域名恒过,runner 上解析失败)。
- 不依赖 git 历史深度(CI 浅克隆);历史版本固化为 `tests/fixtures/`(`deploy_scripts_pre_issue_*` 是冻结副本,**不要改它们的内容**)。
- `tests/conftest.py` 会话级沙箱(`DORAMI_TEST_SANDBOX=1`,xdist 按 worker 分开);新 TestClient 测试仍显式建 tmp sink;不写固定路径、不绑固定端口、不依赖执行顺序。
- 新增 `tests/test_deploy_*.py` 自动落 CI 的 `backend-deploy` job,其余落 `backend-unit`(`tests/test_ci_workflow.py` 守卫划分)。
- 后端测试读 `frontend/` `docs/` `docs-site/` 或仓根 `*.md` → 把测试文件登记进 `scripts/ci_scope.py` 的 `LIGHT_TESTS` 并同步 `ci.yml` 的 `backend-light` 命令(PR 只动这些路径时 CI 跳过重型后端 job;`conftest.py` 读取审计强制,守卫 `tests/test_ci_scope.py`)。
- 本地默认只跑受影响的测试文件,全量交给 CI;改部署脚本 / 迁移本地跑 deploy 侧。
- CI 按 `--shard I/N` 分片(用例函数 nodeid 的 CRC32;`ci.yml` 矩阵,守卫 `tests/test_ci_workflow.py`):用例不得依赖同文件其它用例先跑过。测试会话里 PBKDF2 缺省 1000 轮(`fast_password_hashing`),要真轮数显式传 `iterations=`。
- 测试命令 `cmd > log 2>&1; rc=$?`,不在链里用管道截尾;全量要跑到底,别 `-x`。
- 守卫类测试要做反向对照(把检视方的突变脚本收进仓库)。
