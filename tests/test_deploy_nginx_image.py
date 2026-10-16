"""nginx 镜像里的静态文件必须人人可读(issue #192,v3.65.0 生产实录)。

生产部署进程(docker/dorami-deploy-worker.example)有意 `umask 077`,它 git checkout 出来的新文件 / 改动过的
文件是 0600;构建上下文的权限原样进镜像,nginx 工作进程(uid 101)读不了就回 403——v3.65.0 上线后自托管字体、
manifest 与 PWA 图标全部 403(早年手工克隆的老文件权限正常,所以只有新文件出事)。修在镜像里:
拷入前端产物之后统一放开读权限(目录可进入),与生产机检出时的 umask 无关,也不必放宽部署进程的 umask。
"""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _instructions():
    with open(os.path.join(ROOT, "docker", "nginx.Dockerfile"), encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip() and not line.strip().startswith("#")]


def test_static_files_are_made_world_readable_after_copy():
    lines = _instructions()
    copy_idx = next(
        i for i, line in enumerate(lines)
        if re.fullmatch(r"COPY --from=frontend /build/dist /usr/share/nginx/html", line)
    )
    chmod_idx = next(
        (i for i, line in enumerate(lines) if re.fullmatch(r"RUN chmod -R a\+rX /usr/share/nginx/html", line)),
        None,
    )
    assert chmod_idx is not None, "nginx.Dockerfile 缺少 `RUN chmod -R a+rX /usr/share/nginx/html`"
    assert chmod_idx > copy_idx, "放开读权限必须在拷入前端产物之后"
    # 之后不能再有往 html 目录拷文件的指令,否则新拷进来的又带着构建上下文的权限
    assert not any(
        line.startswith(("COPY", "ADD")) and "/usr/share/nginx/html" in line for line in lines[chmod_idx + 1:]
    ), "放开读权限之后不应再往 /usr/share/nginx/html 拷文件"
