"""版本号——全项目单一事实来源。

管理方式(2026-07 确立):
- 语义化版本(SemVer):MAJOR = 产品形态级改版 / MINOR = 功能波 / PATCH = 修复;
- 改版本只改这里,并同步 pyproject.toml 的 version(项目非 editable install,
  importlib.metadata 读不到包元数据,故以本常量为准);
- `/api/runtime` 透出 version,前端「设置 → 关于」展示;
- 版本号只在发版那一刻改(scripts/release.sh),PR 不各自 bump;annotated git tag
  `v{__version__}` 是唯一的发布单元,部署脚本按 tag 部署(docs/release-process.md)。

纪元回溯:1.x = 采集/归档 CMS 原型(单管理员);2.x = 读者分发平台
(双角色/订阅/RAG/日报/运维,PM2 app 名 dorami-backend-v2 即此纪元遗痕);
3.0.0 = 静默仪器全站重构 + 实体简化/阶段3 收官(style/quiet-instrument 合入 main)。
"""

__version__ = "3.60.1"


def build_info() -> dict:
    """构建来源:生产到底跑的是哪个 tag / 哪个提交(/api/runtime 透出,设置 → 关于 展示)。

    来源优先级:
    1. 环境变量 DORAMI_BUILD_REF / DORAMI_BUILD_SHA——部署脚本按 tag 部署时导出:Docker 路径
       经 build args 烤进镜像(镜像里没有 .git),裸机路径经 ecosystem.config.js 透传给 PM2 进程;
    2. 退回 `git describe`(开发机裸起 / 手工 compose build 未传参且容器里恰有 .git 时);
    3. 都没有 → source="unknown",ref/sha 为空,前端显示「未知」。

    `ref` 恰等于 "v" + __version__ 即「跑的是发布版」;带 -N-gSHA 后缀或 -dirty 即非发布版。
    只在首次调用时探测一次(git 子进程),结果缓存于模块级。
    """
    global _BUILD_INFO
    if _BUILD_INFO is None:
        _BUILD_INFO = _detect_build_info()
    return dict(_BUILD_INFO)


_BUILD_INFO = None


def _detect_build_info() -> dict:
    import os

    ref = os.environ.get("DORAMI_BUILD_REF", "").strip()
    sha = os.environ.get("DORAMI_BUILD_SHA", "").strip()
    if ref or sha:
        return {"ref": ref, "sha": sha, "source": "env"}
    try:
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        run = lambda *args: subprocess.run(  # noqa: E731
            ["git", *args], cwd=root, capture_output=True, text=True, timeout=3, check=True
        ).stdout.strip()
        return {
            "ref": run("describe", "--tags", "--always", "--dirty"),
            "sha": run("rev-parse", "HEAD"),
            "source": "git",
        }
    except Exception:  # git 不存在 / 不是仓库 / 超时——构建来源缺席不是错误
        return {"ref": "", "sha": "", "source": "unknown"}
