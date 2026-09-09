# 个人早报标题中文化(issue #33 §4,v3.51.2)

> 状态:**已落地**(2026-09-09)。上接 v3.45 阅读窗标题同译(`translation_zh_title` 缓存)与
> v3.48 公共日报 `title_cn`;本条复用两者、不新增分析调用。

## 0. 问题

早报条目快照 `snapshot.title` 原样取 `article.title`,英文源条目在「我的早报」里就是英文;
公共日报早有 `title_cn`(editorial_polish 产出),阅读窗切「中文」也早有译名缓存,只是个人早报
两者都没接。

## 1. 拍板

- **展示**:中文主标题 + 英文原标题作副标题小字(桌面/移动同一 `BriefCard`,`.brief-card-title-orig`
  沿阅读窗 `.reader-pane-title-orig` 的视觉语言:衬线、muted、降一档;头条卡略大)。
- **来源按成本从低到高**(`services/personal_digest_titles.py`):
  1. 文章 `extensions_json.translation_zh_title`(指纹与当前标题一致才算,与阅读窗同一判定);
  2. 近三期公共日报 `extensions.items[].title_cn`(同篇 `id`;编辑标题只作展示,**不写回**译名缓存——
     它是编辑改写的头条名不是译文);
  3. 编排后批量译标题:aux 轻模型、阅读窗同一提示词 `TRANSLATE_TITLE_SYSTEM_PROMPT`、并发受
     `map_concurrency`、整体 **20s 预算**(超时条目回退原标题,下次再补);译文写回
     `translation_zh_title` + 指纹,阅读窗与其他读者的早报都受益。
- **纪律**:中文标题(`looks_chinese`)不译不画副标题;任何一步失败回退原标题;LLM 未配置只做
  1/2 两级;**不并入入库分析调用**(issue #22 结论)。
- **接入点**:`personal_briefs.process_pending_edition` 在 edition 落成 ready/degraded **之后**调
  `localize_edition_titles`——只补展示字段 `snapshot.title_zh`,异常只记警告、绝不把已完成的版本打成
  failed;幂等(已带 `title_zh` 的条目跳过)。打开/重编/08:30 三条路同一钩子。
- **计量**:新用途 `personal_digest_title`(`ai_usage.VALID_PURPOSES`),归属 `system`,不进读者面预算
  三处(逐用户限额/全站日预算/READER_AI_BUDGET_PURPOSES)——它是系统任务,不受读者 AI 总闸约束,
  与公共日报同口径。
- **同步/异步**:编排链路是同步代码(请求线程池 / APScheduler 同步 job),`_run_async` 在无事件循环时
  `asyncio.run`,误从循环线程调用时退到独立线程,不抛 "running event loop"。

## 2. 边界(有意)

- 历史 edition 不回填:快照不可变,旧版面保持当时形态;新生成的每一版都带中文标题。
- 预算内译不完的条目本版就是英文,不做后台补齐(读者重编即补;避免给 edition 引入「事后变更」)。
- 公共日报 `title_cn` 与译名缓存可能措辞不同(编辑标题 vs 直译),同一篇在公共日报与早报里可能
  出现两种中文标题——两者本就是两个产品,不强求一致。

## 3. 验证

- `tests/test_personal_digest_titles.py`:三级来源按成本顺序命中且只有缺来源的条目调 LLM(aux 模型、
  系统用途)、中文跳过、译文写回缓存而日报编辑标题不写回、单条失败回退、幂等、未配置只做两级、
  预算超时不阻塞、`_run_async` 在事件循环内可用。
- `tests/test_analysis_personal_api.py`:ensure 后条目带 `title_zh`,重编命中缓存不再调 LLM;
  中文化整体异常时版本照常 ready、无 `title_zh`。
