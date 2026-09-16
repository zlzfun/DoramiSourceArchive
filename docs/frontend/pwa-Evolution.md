# PWA Evolution

## 2026-09-16 · #85 安装层与隔离真机预览

- 发生：用户确认先实现现有阅读器的安装层，并提供 MatePad mini / 鸿蒙 7 / 华为浏览器 6.1.7.303；授权通过 cloudflared 暴露隔离预览，由平板参与验收。
- 分析：相比完整 Workbox 预缓存，内嵌恢复页的最小 SW 足够满足联网阅读与断网提示，且没有私人内容清理、旧壳与新资源混用的额外状态。桌面平板也需要可达入口；浏览器快捷方式不能等同独立应用。
- 改变：增加 Manifest／图标、移动入口与设置内联指引、最小 SW、更新提示及部署重新校验。预览沿用真实 E2E 的隔离配置与播种，采用随机读者密码和每次新签名密钥、精确 Host 白名单、限时进程回收。

### 实际产物纠偏

- 第一轮 E2E 虽通过，实际截图中的移动指引却被祖先容器裁切。将该浮层通过 portal 挂到 `document.body`；增加边界与关闭按钮命中断言，并故意移出屏幕证明断言会拒绝。设置中使用内联展开，避免嵌套弹窗。修正后重新查看亮／暗色、窄屏和宽屏产物。
- 第一轮 maskable 图标出现内嵌方块边缘；在安全留白内羽化原图背景边缘，重新生成并直接查看成品。
- Chromium 重载时，快速会话响应会卸载加载 Logo，留下 `net::ERR_ABORTED` 图片请求。trace 确认后，仅对 `/brand/` 的已取消图片单独记账，并验证它不是当前可见的破损图片；其他失败继续报错。
- 第一次隧道强制 HTTP/2，真实日志报告 TLS EOF、TCP 7844 不可达但 QUIC 可达。移除协议强制，第二次 Quick Tunnel 成功。原失败日志保留，未修改用户现有 cloudflared 配置。

### 本轮观测

- `npm run lint`、13 项 Node 测试、23 项隔离／预览／配置输出／部署库 pytest 通过；`bash -n deploy.sh` 通过。
- `cd frontend && npm run test:e2e` 实跑通过，17 项阅读／PWA 检查；结果目录 `tmp/e2e/reader-auh_gmzg/`。构建入口 SHA-256：`d9c91a9492d264e0f04cb579f8e23b7c71f9b2ad9ad4d38d7895145d364ab1c5`。
- 反向检查：私有/API/MCP/跨源/资源/写请求均绕过 SW；以下破坏性输入被拒绝：错误密码；不归属沙箱的配置与已有数据库；越界预览时长；恶意 Host；移出视口的导航和安装弹窗；错误的缓存期或资源 SPA 回退配置。SW 单元测试构造的 502 原样透传，浏览器实测离线 API 没有伪装成成功 HTML；登录／退出前后 CacheStorage 均为空。
- 实际看过修正后的移动华为指引、暗色 iOS 指引、宽屏设置、更新提示、断网页和 maskable 图标。截图与 trace 保留，证据目录附 `evidence-sha256.json` 并核对；失败轮次独立保留。
- 公网预览与本次 index／SW／Manifest 哈希一致；桌面 Chromium 通过公网 HTTPS 完成真实登录、取得 SW 控制并打开华为指引。结果和截图在 `tmp/pwa-preview/session-9ez9t_l1/`。这是公网应用验证，**不是 Huawei 真机认证**。
- Nginx 三种模式运行真实配置输出函数并检查产物，Docker 检查配置文本；本机没有 Nginx／Docker 可执行文件，未作部署运行验证。华为、iOS 和 Android Chrome 真机安装、软键盘、安全区、外链返回及系统重启恢复仍待实测，#85 尚不据此关闭。

### 平台指引依据

- [Apple：将网站变成 iPhone 上的 App](https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios)：分享 → 添加到主屏幕，新版可开启 Open as Web App。
- [华为：浏览器设置网站桌面快捷方式](https://consumer.huawei.com/cn/support/content/zh-cn16032141/)：HarmonyOS 5.0 及以上的官方快捷方式步骤；它不证明本次设备的独立窗口能力。
