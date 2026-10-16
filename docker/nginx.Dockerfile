# 前端多阶段构建 + Nginx 静态服务镜像。
# 前端在镜像构建期产出(取代 deploy.sh 在生产机上现场 npm install/build),
# 静态资产烤进镜像——发布即原子(整镜像切换,不存在 rm -rf 后 cp 的半套窗口)。

FROM node:22-bookworm-slim AS frontend
WORKDIR /build
ARG NPM_REGISTRY=https://registry.npmjs.org
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund --registry=${NPM_REGISTRY}
COPY frontend ./
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend /build/dist /usr/share/nginx/html
# 静态文件统一放开读权限(issue #192):生产部署进程有意 umask 077,它检出的新文件是 0600,构建上下文的权限
# 原样进镜像,nginx 工作进程(uid 101)读不了就回 403(v3.65.0 自托管字体、manifest、PWA 图标实录)。
# 修在镜像里,与检出时的 umask 无关;守卫 tests/test_deploy_nginx_image.py
RUN chmod -R a+rX /usr/share/nginx/html
