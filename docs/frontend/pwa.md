# 安装到主屏幕

[#85](https://github.com/zlzfun/DoramiSourceArchive/issues/85) 为现有阅读器提供安装入口、独立窗口声明和联网恢复体验。移动端从「我的 → 添加到主屏幕」进入；桌面及宽屏平板从「设置 → 外观」展开步骤。已有的认证、响应式布局与阅读状态继续承担阅读链路。

## 安装与运行

- Chromium 收到 `beforeinstallprompt` 时，由用户点击调用系统安装；取消后回到原页面。没有安装事件或调用失败时提供手动指引。
- iOS 提供 Safari 分享菜单步骤；华为提供浏览器「添加至桌面」步骤。UA 只选择默认文案，用户可切换指引；独立窗口能力以具体设备实测为准。
- 独立显示模式或本页收到 `appinstalled` 时隐藏入口。普通浏览器中的跨会话安装状态由浏览器能力决定；新安装窗口可能需要重新登录。
- 应用以根路径 `/` 为身份、作用域和启动入口。图标包括 192、512、180px Apple touch 及独立的 512px maskable 图标。
- 正常导航实时联网；断网导航显示静态恢复页，重试保留当前路径。在已打开页面离线时显示网络状态。
- 新构建生成新 SW 指纹；更新立即激活但保留当前页面，用户自行刷新。回到前台／联网时最多每五分钟检查一次更新。

## 实现边界

| 职责 | 入口 |
|---|---|
| Manifest、图标 | `frontend/public/manifest.webmanifest`、`frontend/public/brand/pwa-*` |
| 安装事件、运行与更新状态 | `frontend/src/pwa.js` |
| 操作入口与平台指引 | `frontend/src/components/InstallApp.jsx` |
| 网络／更新提示 | `frontend/src/components/PwaStatus.jsx` |
| 生产构建 SW、内嵌断网页 | `frontend/pwa/build.mjs`、`sw.js`、`offline.html` |
| 部署重新校验 | `deploy.sh` 三种站点配置、`docker/nginx.conf` |

SW 只处理同源 GET 页面导航的网络异常。API、MCP、静态资源及写请求走原网络路径；HTTP 错误保留原响应。没有 CacheStorage、应用壳或文章离线存储；成本是首次访问下载一个内含恢复页的小型 SW，收益是断网启动有明确出口。完整阅读依赖网络。

`index.html`、`sw.js` 和 Manifest 重新校验；缺失 SW／Manifest 返回 404，避免 SPA HTML 冒充资源。SW 仅在生产构建与安全上下文中注册；开发模式用于普通 UI 开发，PWA 验证使用生产构建预览。

## 验证与真机入口

自动化已接入 [阅读器 E2E](./e2e.md)。单元测试覆盖 SW 网络边界、图标尺寸及构建指纹；浏览器测试覆盖安装取消／失败、独立模式、断网深链恢复、真实 SW 更新和登录恢复。安装事件与独立模式的模拟只验证应用反应，真机安装单独验收。

在仓库根目录，使用已有 `.venv`、`frontend/node_modules`、Playwright Chromium 与 `cloudflared`：

```bash
.venv/bin/python scripts/preview_pwa.py --minutes 120
```

该入口创建合成内容和随机密码的普通读者、独立签名密钥、隔离构建与临时数据库，再启动 Quick Tunnel。只允许本地和当次精确隧道 Host；公网 `index.html`、SW、Manifest 哈希核对通过才输出 Ready。网址、临时凭据和日志保存在 `tmp/pwa-preview/session-*/`，不要提交或公开上传该目录。到期、Ctrl+C 或 SIGTERM 会停止所属进程组并移除沙箱；日志保留。重新启动 Quick Tunnel 会更换站点身份，需重新添加桌面图标。

### 真机待验收

用户提供的设备组合：**MatePad mini / 鸿蒙 7 / 华为浏览器 6.1.7.303**。记录以下结果，并附系统菜单和启动窗口截图：

1. 登录后找到安装指引，用浏览器菜单添加桌面图标。
2. 从图标启动，观察图标、名称和是否仍有浏览器地址栏；关闭后重开，检查登录与阅读。
3. 横竖屏切换、正文滚动、返回列表、软键盘收起后，检查主要操作可达。
4. 首次联网打开后，再断网刷新：观察恢复页；联网后重试。

当前自动化不替代华为、iOS、Android Chrome 真机结论；也未在实际 Nginx 或 Docker 部署执行。实测记录和设计理由见 [pwa-Evolution.md](./pwa-Evolution.md)。
