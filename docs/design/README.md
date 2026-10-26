# 设计样页索引(design/)

> 状态:耐久参考。HTML 样页是各波次拍板的依据,`frontend/src/index.css` 注释以「刻度 1:1 取自 …」引用;实现后样页不再更新,**现行 UI 以代码为准**。阅读器 / 设置柜 / 发现页等后续样页发布在 Claude Artifact(链接见各波记录)。历史探索稿在 [`../archive/README.md`](../archive/README.md#design--历史设计样页2026-10-21-归档)。`pr-assets/<PR>-<slug>/` 是 PR 配图,不是样页。

| 样页 | 波次 / issue | 说明 |
|---|---|---|
| [issue-218-title-translation/](./issue-218-title-translation/README.md) | issue #218（R2 正式版已验收） | 默认中文标题设置、标题待译回退、正文手动翻译、连续前缀与逐段骨架原位替换两种呈现对比；桌面/手机、亮暗主题，模拟数据与进度，未接后台 |
| `guest-mode/`(README + index / frame) | issue #213(已落地) | 游客模式九个场景:深链直达 / 登录页「先逛逛」/ 登录提示 / 游客版早报 / 发现页 / 分享 / AI 试用 / 设置与我的 / 运维游客区;底稿为现网 DOM 快照 |
| `issue-121-hot-events/`(index + direction-a/b/c) | issue #121(前端搁置,2026-10-23) | 热点事件榜三方向样页:A 同构榜行 / B 事件卡 / C 头版;桌面嵌 RankingsPage 壳、手机壳、亮暗、四种榜况(6 / 7 / 3 件 / 空榜)、「新」与排名变化、「接前报」链;数据为调研金标真实事件。用户目检:三者都待商榷,B 较优但非定稿,留待文章榜 / 播客榜 / 热点统一重构时重出 |
| `podcast-player/`(README + index / global / pane) | issue #199(v3.66) | 播客区方案 A / B / C × 状态 s1–s6,全局播放条条位 × 配色;拍板 C + 内容区浮层 + 封面模糊 |
| `social-translate/`(README + index / frame) | issue #84(v3.66) | 社交流翻译入口与译文呈现 |
| `visual-refine-final/`(index / type / color / reader / brief / console / details + shots) | issue #184(v3.65) | 视觉精修定稿:字体 / 中性色三套 / 阅读器 / 夜航早报 / 管理面;字体 data URI 内嵌可离线开 |
| `dorami-classification-scores-quiet.html` | issue #178(v3.6x) | 内容分类与双评分:类型签 / 筛选带 / 评分卡切维度 |
| `dorami-admin-podcast-taxonomy-quiet.html` | issue #76(v3.58) | 运维面「播客」与「分析与标签」区,含 2026-09-15 目检返修 |
| `dorami-score-tiers-quiet.html` | issue #54(v3.53) | 新闻价值分按分值分档着色,灰线 6.0 / 分档 / 陡峭 |
| `dorami-brief-grid-quiet.html` | issue #74 | 分值驱动网格(已随 #184 退役,留作记录) |
| `dorami-brief-personal-quiet.html` | issue #33 §3(v3.54) | 个人早报个性化页面:编排说明行、选篇理由 |
| `dorami-brief-quiet.html` | issue #23(落地页早报) | 卡片式日报:报头 / 分节卡片网格 / 同日多版胶囊 |
| `dorami-interest-axis-quiet.html`(五稿,现行) / `dorami-interest-lens-quiet.html`(四稿) / `dorami-interest-quiet.html` | issue #27 / #23 | 左栏一根轴「订阅 | 兴趣」;四稿含三次被否方案注记;兴趣页 |
| `dorami-onboarding-quiet.html` | issue #9(v3.45) | 视图轨微标签 / 轨底改制 / 标题下动作行提案 |
| `dorami-ask-quiet.html` | v3.32 | 问答面板重设计(引用联动 `[n]`、范围档、阶段化等待态) |
| `dorami-ai-identity-quiet.html` | v3.33 | AI 渐变身份色 `--grad-ai` |
| `dorami-context-menu-quiet.html` | v3.28 | 右键上下文菜单 |
| `dorami-mobile-quiet.html` | v3.29 | 移动端 H5 阅读器壳 |
| `dorami-social-quiet.html` | v3.12 | 社交媒体卡片流 |
| `dorami-pane-head-quiet.html` | issue #23 第三项 | 阅读窗标题区 |
| `dorami-integrations-quiet.html` / `dorami-settings-quiet.html` | v3.9 | 接入集成并入设置柜 |
| `dorami-admin-quiet.html` / `dorami-runs-quiet.html` / `dorami-job-roster-quiet.html` / `dorami-job-editor-quiet.html` | v3.0 静默仪器 | 运维 / 运行页 / 采集任务名册与编辑器 |
| `dorami-login-constellation-script.md` | v3.13 | 登录页星座演出的分镜脚本(品牌豁免动效,实现 `LoginScreen.jsx`) |

未入库的样页:`dorami-admin-ia-quiet.html`(issue #114 运维页信息架构,未立项)、`dorami-dept-pies-quiet.html`、`dorami-search-scope-quiet.html`(issue #94,待拍板)等仍在各会话工作区,拍板后随波次入库。
