# 设计样页索引(design/)

> 状态:耐久参考。HTML 样页是各波次拍板的依据,`frontend/src/index.css` 注释以「刻度 1:1 取自 …」引用;实现后样页不再更新,**现行 UI 以代码为准**。阅读器 / 设置柜 / 发现页等后续样页发布在 Claude Artifact(链接见各波记录)。历史探索稿在 [`../archive/README.md`](../archive/README.md#design--历史设计样页2026-10-21-归档)。`pr-assets/<PR>-<slug>/` 是 PR 配图,不是样页。

| 样页 | 波次 / issue | 说明 |
|---|---|---|
| [issue-230-mobile/](./issue-230-mobile/README.md) | issue #230(手机端系统性优化波,方向 C 已定稿;第 9 轮已回填最终样页,开发以它为准;阶段 3 实现中) | 移动端视觉方向「夜航」单文件样页 `index.html`(A 纸面 / B 仪器已舍弃,见提交 18f0064 历史):第 2 轮参考 Folo 手机版改成功能模块底栏(内容 · 订阅 · 榜单 · 设置,内容类型上移到页头,第 3 轮定为顶栏中段的转盘并取消视图未读点,第 5 轮改为三项固定位、选中放大,来源底页变成订阅 Tab,「我的」并进设置;第 6 轮底栏定为 早报 · 内容 · 订阅 · 榜单,设置改由四个 Tab 页头的头像推入;第 7 轮四个 Tab 页头合成一行,标题位即主切换;第 9 轮最终样页:播放器回填 B 悬浮卡 + B1 一排五钮、底部层抓手都是真按钮、手机设置去 OPML 与接入集成、正文 AI 速读卡里的听导读与播客条目对齐 main);17 屏(早报 / 往期月历、内容·文章 / 播客 / 社交、正文 / 读到文末 / ⋯ 菜单 / 长按、播放·展开卡 / 正文页展开卡、订阅 / 来源流 / 兴趣、榜单、设置(从头像进入)/ 设置栈)× 亮暗两台手机并排可点,早报定为独立 Tab、往期定为底页月历、正文去底部坞,读者 / 游客可切,机型宽度 360 / 390 / 430,`data-motion` 与中性色开关;README 有两轮返修逐条记录(含「全部 / 未读」根因与新形态)、往期月历规则、动效清单、增删表与拍板清单;数据取自 visual-refine-final 的本地库快照与 podcast-player 的真实播客 / 推文;含细节样页 `details/`(一页敲定一个小点,首个是内容 Tab 视图切换器,已选 B 转盘并回写,第 5 轮改为三项固定位;第二个是底栏怎么分 `tabbar.html`,A 设置进头像 / B 榜单并入订阅 / C 两者都做 / D 五项不变只减负,已选 A 并回写;第三个是四个 Tab 页头合成一行 `tab-header.html`,A 同行标题 / B 标题即切换 / C 胶囊页头,已选 B 并回写;第四个是播放坞与展开页 `player.html`,A 整理底页 / B 悬浮卡 / C 坞内展开,已选 B 并回写;第五个是悬浮卡里的按钮 `player-card.html`,B1 一排五钮 / B2 标签键 / B3 控制带 / B4 封面即播放,已选 B1 并回写) |
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
