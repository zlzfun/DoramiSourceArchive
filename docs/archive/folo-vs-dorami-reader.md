# Folo 对照分析:用户面(阅读器)可用性差距

> 性质:**竞品对照分析**(结论输入 [`reader-usability-plan.md`](reader-usability-plan.md) 的迭代计划)。
> 评估对象:用户面阅读器(`frontend/src/components/ReaderTab.jsx`)与内置内容源体系(`src/fetchers/impl/`)。
> 参照对象:[Folo](https://github.com/RSSNext/folo)(RSSNext,38k+ star,AGPL-3.0,"the AI RSS Reader")。
> 触发:用户反馈「源数量不足且大部分不适合阅读(changelog 等)」+「阅读器待优化」,并指定以 Folo 为重点参照。
> 日期:2026-07-16。源清单与代码锚点经代码核实;Folo 特性来自其 GitHub README、Show HN 帖与第三方评测。

## 0. 一句话结论

Dorami 阅读器与 Folo 的差距**不在界面**(三栏布局、订阅管理、收藏、翻译、QA 已形似),而在两层机制:**阅读循环机制**(未读/新内容感知——让用户每天回来的心脏)与**内容形态学**(通知型内容与文章分流、AI 摘要前置)。这两层所需的数据模型与 LLM 基建项目里大多已存在,主要是「接线」工作。「源大部分不适合阅读」的问题一半是**形态错配**而非源质量问题——changelog/Release 不是坏源,是被放错了容器。

## 1. Folo 的产品内核(调研结论)

1. **统一时间线 + 按内容形态分视图**。左栏顶部图标排是其核心信息架构:**文章 / 社交动态 / 图片 / 视频 / 音频 / 通知** 六种视图。同一批订阅按内容形态自动分流:博客进文章流;GitHub Releases、changelog 类进 **通知(Notifications)视图**——短条目、只扫不读。这是它对「不适合阅读的源」的解法:不是不收,而是换一种消费形态。
2. **AI 作为默认阅读路径**。打开文章即见「AI 总结」卡片 + Ask AI 追问 + 翻译;2025 年末 Show HN 的卖点是 "summarizes timeline and sends daily AI digest"(摘要时间线 + 每日 AI 日报)。
3. **未读驱动的阅读循环**。未读计数、打开自动标读、一键全部标读、只看未读——RSS 阅读器让用户形成每日回访习惯的基本机制。
4. **RSSHub 生态做源发现**。背靠 RSSHub 网络(1200+ 网站、5000+ 实例)+ 社区 lists/collections,源丰富度靠生态而非官方逐个硬编码。

参考资料:[Folo GitHub](https://github.com/RSSNext/folo) · [Show HN: Folo daily AI digest](https://news.ycombinator.com/item?id=46033915) · [DEV 社区评测](https://dev.to/wonderlab/open-source-project-of-the-day-part-19-folo-ai-powered-next-generation-information-reader-3c02) · [app.folo.is/rsshub](https://app.folo.is/rsshub)

## 2. Dorami 现状盘点

### 2.1 内置源构成(经代码核实,2026-07-16)

内置 28 个 `source_id`,其中 5 个是 gate off 的 `generic_*` 模板,**实际预设源 23 个**:

| 形态 | 源 | 数量 |
|---|---|---|
| **文章型**(适合阅读) | OpenAI News、Anthropic News、Claude Blog、Qwen Blog、ByteDance Seed Research、量子位、新智元、IT之家 AI、Google Gemini Models Blog | ~9 |
| **通知型**(适合扫不适合读) | `docs_*` changelog ×6(Codex/Claude Code/Gemma/xAI/DeepSeek API/Z.ai)、Cursor Changelog、GitHub Releases ×3(OpenCode/OpenClaw/Hermes)、DeepSeek 新仓库、DeepSeek HF 新模型 | ~12 |
| **半结构化** | Hacker News: AI(discovery 源,外链帖无正文)、HF Daily Papers | 2 |

**约一半是通知型**,与用户判断一致。且问题比「数量不足」更结构性:通知型条目和深度文章挤在同一列表里以文章卡形态出现,拉低整个流的「可读感」。

**关键事实**:6 个 `docs_*` changelog 源的 `content_type` 也是 `web_article`(独立 content_type 只有 `github_release` / `github_repository` / `hf_model`),所以**形态分流不能只靠 `content_type`,需要源级形态标记**(见计划迭代 A)。

### 2.2 阅读器现状(`ReaderTab.jsx`,731 行)

已有:三栏布局(可折叠/专注阅读)、订阅管理(左栏)、收藏、关键词搜索、译为中文(缓存于 `extensions_json.translation_zh`)、哆啦美 QA 浮层、骨架屏、阅读进度线、正文懒加载与竞态防护。**界面层已与 Folo 形似。**

缺失(机制层):

| 缺口 | 现状 | Folo 对应 |
|---|---|---|
| **未读体系** | 完全没有。`POST /api/reader/articles/{id}/read` 只做 admin 观测计量(`ReaderReadRecord` 按天×用户×源聚合,`db.py:277`),不回流用户 UI | 未读计数/自动标读/全部标读/只看未读 |
| **新内容感知** | 需手动刷新/切源 | 时间线自动更新 |
| **AI 摘要** | 有翻译+QA,无摘要;列表卡摘要用正文截断(`content_preview`),对英文长文几乎无信息量 | 打开即见 AI 总结卡片 |
| **形态分视图** | 所有内容单一文章流 | 文章/通知等六视图 |
| **日报入口** | `dorami_daily_brief` 只是一个普通源 | daily AI digest 是核心卖点 |
| **源发现生态** | 封闭 23 预设;`source_builder` 与 `generic_*` 模板已具备但 gate off | RSSHub 1200+ 网站 |
| 键盘导航 | 无 | j/k 等标配 |
| 语义搜索入口 | 搜索框仅关键词;`/api/vector/search` 对 user 已就绪且硬 scope 到订阅 | — |
| 移动端 | 三栏窄屏不可用 | 全平台 |
| 无正文降级 | 一句提示文案(`ReaderTab.jsx` 右栏兜底) | — |

## 3. 优化方向(按杠杆排序)

- **方向 B · 未读体系**(杠杆最大,建议最先):补上阅读循环的心脏。数据层需 per-user 已读状态;顺带解决新内容感知。
- **方向 A · 内容形态分流**:「动态/通知」视图与「文章」流分离。双维内容标识(`content_type` × `source_id`)是现成地基,一招消解「源不适合阅读」的一半问题。
- **方向 C · AI 摘要前置 + 日报置顶**:`daily_brief` 的 map 阶段本就是 per-article summarize;`reader_ai.py` 的翻译缓存模式(`extensions_json.translation_zh`)可直接复制为 `summary_zh`;日报提升为阅读器一等公民。
- **方向 D · 源扩容**(与主线并行,纯内容工作):短期扩 curated 文章源;中期把 RSSHub 作为 `generic_rss` 上游;`source_builder` 可考虑以「admin 审核入库」受控启用。
- **方向 E · 体验细节波**(收尾):键盘导航(j/k/m/s/v)、无正文条目「抓取全文」按钮(走现有 `article_extractor`/crawl4ai 按需回填)、预计阅读时长、日期分组分割线、移动端响应式降级。
- **方向 F · 语义搜索入阅读器**(低成本):搜索框加「语义」切换(RAG 开启时),把已建好的 RAG 能力第一次真正交到读者手上。

**主线定为 B → A → C**,详见 [`reader-usability-plan.md`](reader-usability-plan.md)。
