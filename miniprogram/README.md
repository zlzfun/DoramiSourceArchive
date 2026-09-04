# 哆啦美阅读器 · 微信小程序端(基础版)

对应 [Issue #17](https://github.com/zlzfun/DoramiSourceArchive/issues/17),方案见 [`docs/wechat-miniprogram-wave-plan.md`](../docs/wechat-miniprogram-wave-plan.md)。
Taro 4 + React(JS/JSX,与 `frontend/` 同语法),独立 `package.json`,不进 `frontend/` 的构建。

## 本地开发

```bash
cd miniprogram
npm install
npm run dev:weapp          # 产出 dist/,watch 模式
```

1. 微信开发者工具 → 导入项目 → 选择 **本目录**(`project.config.json` 指向 `dist/`),AppID 填自己的(或用测试号)。
   私有 AppID 写在 `project.private.config.json`(已 gitignore)。
2. 后端地址在 `.env.development`(默认 `http://127.0.0.1:8088`,真机调试改成局域网 IP);
   开发者工具「详情 → 本地设置」勾选 **不校验合法域名**。
3. 后端须为含小程序载体的版本(`POST /api/auth/login` 回 `session_token`;`GET /api/reader/articles/{id}/render`;`GET /api/public/media`)。

## 结构

```
src/
├── app.js / app.config.js / theme.json   # 入口 / 页面与 tabBar / 亮暗导航栏色
├── app.scss                              # 设计令牌(自 frontend/src/index.css 移植,亮暗成对)+ 通用原语
├── config.js                             # API_ORIGIN(TARO_APP_API_ORIGIN)
├── api/request.js                        # Taro.request 封装:Bearer 挂头、401 回登录门
├── api/index.js                          # 端点函数(命名对齐 frontend/src/api.js)
├── store/session.js                      # 会话 token / 用户 / runtime
├── store/reader.js                       # 源目录·订阅 / 未读 / 收藏 / 各容器过滤器
├── shared/                               # 自 frontend 复制的 DOM 无关纯函数(改动两侧同步)
├── features/bootstrap.js                 # 登录后会话引导
├── features/feed/FeedPage.jsx            # 条目流(四个 tabBar 页共用)
├── components/                           # TopBar / ArticleCard / SocialCard / ListSkeleton
└── pages/
    ├── feed/{article,podcast,bulletin,social}   # tabBar ×4(薄壳)
    ├── article                                  # 正文页(rich-text + 播客播放器 + 分享)
    ├── sources                                  # 源过滤(全部/收藏/按角色分组的已订阅源;长按退订)
    ├── discover                                 # 发现(源目录 + 一键订阅)
    ├── me                                       # 我的
    └── login                                    # 登录门(redirect 回落)
```

## 已知边界(方案 §3.3 / §10)

- 正文内链接不可点(`rich-text` 限制),「复制原文链接」由页面按钮承担;公式按原文文本保留。
- tabBar 暂为文字无图标;个人早报页、合集视图、AI 翻译/速读为 P2。
- 会话 7 天到期后回登录门,无静默续期。
