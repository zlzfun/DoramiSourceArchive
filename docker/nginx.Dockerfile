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
