---
paths:
  - "deploy-docker.sh"
  - "deploy.sh"
  - "scripts/deploy-*.sh"
  - "scripts/release.sh"
  - "scripts/verify-release-ref.sh"
  - "docker/**"
  - ".github/workflows/**"
---

# 部署脚本与流水线硬规则(正文:docs/ops/release-process.md、docs/ops/deploy-*.md)

- **不写 `| grep -q` 当条件**(`pipefail` 下生产者吃 SIGPIPE、条件翻转,issue #150);改读到 EOF / here-string。
- bash 3.2(macOS 测试机)兼容:中文文案里变量一律 `${NAME}`;`$( case … )` 先算变量;`cat >>` 并行追加会让同批编辑失效。
- tag 即发布:两条部署脚本只认正式 tag(`--here` 显式例外并标注非发布版);只部署宣告了 `DORAMI_DEPLOY_PROTOCOL` 的 tag;不自动回滚;Docker 与裸机共用一把锁。
- 流水线:`release.yml` 建 Release → `deploy.yml` 停在 Environment `production` 等批准(`secrets: inherit` 必须);`actions/checkout` 会改写 tag 引用,核验脚本勿 `--tags`。
- 守卫:`tests/test_deploy_scripts.py` / `tests/test_deploy_baremetal.py` / `tests/test_ci_workflow.py`(CI 两 job 划分逐字同源、汇总门不可被 skip);改 `ci.yml` 必跑后者。
- 镜像依赖以 `docker/requirements*.txt`(`uv export` 产物)为版本事实来源;改依赖后必须重导出(`tests/test_docker_requirements.py`)。
- nginx 镜像拷入静态文件后统一 `chmod a+rX`(#192);部署后核对公网 `/fonts/*`、`/manifest.webmanifest` 200,不能只看 `/api/health`。
