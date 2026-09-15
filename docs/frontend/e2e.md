# 阅读器端到端测试

[Issue #90](https://github.com/zlzfun/DoramiSourceArchive/issues/90) 的首个切片：移动读者主链路，兼顾 [#86](https://github.com/zlzfun/DoramiSourceArchive/issues/86) 响应式导航和 [#2](https://github.com/zlzfun/DoramiSourceArchive/issues/2) 登录门深链回归。使用已有 Python Playwright；管理端、个人早报和 CI 集成留在 #90 后续范围。

## 一条命令

依赖已装好的仓库 `.venv` 和 `frontend/node_modules`；默认使用 Playwright Chromium。首次安装浏览器，在仓库根目录运行：

```bash
.venv/bin/python -m playwright install chromium
```

执行测试：

```bash
cd frontend
npm run test:e2e
```

可选 `npm run test:e2e -- --headed` 显示浏览器，或 `--channel chrome` 使用已安装的 Google Chrome。当前进程组清理按 macOS/Linux 实现；Windows 未验证。

## 测什么

主流程通过界面完成登录、来源筛选、阅读、收藏、返回和设置操作：

- 错误密码被真实后端拒绝；正确密码进入阅读器。
- 长来源名保持筛选按钮单行；选源后抽屉关闭、标题和真实列表一致。
- 列表滚动而文档不滚动，底栏贴住可视区域底部，各按钮都能命中；下一页来自真实 API。
- 阅读后返回原列表偏移；跨宽度后仍能看到原条目，正文保留进度且停在相邻章节，宽屏恢复四栏、历史不积累空返回步骤。
- 空态、断网反馈与恢复不混入旧条目；提示不遮挡底部导航。
- 刷新后仍已登录且收藏保留；独立读取 SQLite 确认读态和收藏落库。
- 在登录门收到同标签页 hash 深链，登录后打开目标文章。

操作等待具体响应或界面状态，不使用固定睡眠等待页面就绪。点击用 `tap`，内容滚动用浏览器 wheel；这是 Chromium 手机模拟，不是真机触摸手势、软键盘或安全区验收。截图将有限 CSS 动画推进到结束后保存；交互本身保留应用动画。

## 隔离与结果

[`scripts/check_mobile_reader_e2e.py`](../../scripts/check_mobile_reader_e2e.py) 每次新建临时配置和数据库，运行迁移并播种两个来源、63 篇合成文章和一个普通读者。以实际 `src/main.py` 启动 FastAPI，前端重新构建到独立临时目录，通过 `vite preview` 同源代理访问后端。业务 API 不替换响应；故障用例只把浏览器网络临时设为离线。

测试不接受外部服务或数据库 URL；子进程环境采用白名单，不继承部署配置、云凭据或模型配置。媒体、语音产物路径均在沙箱，reader 运行角色不启动采集任务。测试结束或失败时关闭所属进程组、删除临时数据库与构建目录，保留用户原来的开发服务和 `frontend/dist`。

每次结果写入 `tmp/e2e/reader-*/`：

- `result.json`：结论、构建指纹、检查项、阅读位置和请求审计。
- `mobile-*.png` / `desktop-reader.png`：实际渲染产物。
- 失败时的 `*-trace.zip`、截图、DOM 几何和 `error.txt`，以及构建／服务日志。

默认只保留失败 trace，不录视频。trace 含一次性测试账号和合成内容，没有生产会话；分享产物前仍应检查内容。

## 验证状态

本地已实跑主命令；隔离守卫的 7 项 pytest、前端 lint 和 7 项 Node 测试通过。故意将底栏移出视口 96px 时布局断言拒绝；缺失沙箱标记、配置错指、数据库错指、已有数据库均被拒绝，忽略 SIGTERM 的测试子进程也被清理。

这不是全站 E2E 覆盖，也未替代 WebKit / 手机真机验收。演进及本轮发现的问题见 [e2e-Evolution.md](./e2e-Evolution.md)。
