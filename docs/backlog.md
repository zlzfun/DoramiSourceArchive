# 待办栈(Backlog)

> 性质:**跨波次的待办总账**——「进行中 / 排队中 / 展望」三档,动工时在此标注并链接方案文档。
> 波内逐项 ☐ 以各方案文档为准;已完结波次的执行记录在 `docs/archive/`(索引见其 README)。
> 建立于 2026-07-19(用户指示:待办栈落文件)。

## 进行中

- (暂无)

## 排队中(用户拍板、未动工)

- ☐ **17 个 incubating 源观察期转正评审**
  转正流程见 `docs/sources/curation_policy.md`「Incubation」节;
  Reddit 转正门槛 = 生产出口 IP 复验 429。转正时顺带更新 `docs/sources/node_catalog_and_risks.md` 快照。
- ☐ **日报源手工名单实践观察**(v3.3.0 落地 `daily_brief_source_ids` KV,观察实际日报质量后调整名单)

## 展望(用户表态、未立项)

- ◇ **跨容器去重**(X 社交波遗留):同一次发布 = 一条推 + 一篇 blog。
  首版不做;观察期用 `x_openai` 对照组量化与 `rss_openai_news` 的重复率后再定策略
  (候选:复用日报的 `dedup_clusters`)。见 `docs/social-x-wave-plan.md` §1「重叠率备案」。
- ◇ **X 第二批账号**(观察期后按数据决定):候补名单与不收理由见方案文档 §1。
  同脉络的旧待办「管理面账号池(凭据池/轮换/健康探测)」**已作废**——X 改按量付费后
  官方 API 路径不需要真实账号 Cookie,前提消失。
- ◇ **archive sync 媒体伴随包**(图床波遗留):collector→reader 的 JSONL 契约尚不携带 `data/media/`
  缓存文件,内网 reader 部署要吃到图,需要媒体导出/导入通道(tar 流或清单+分块)。单机部署无此需求。
- ◇ **媒体库容量策略**(图床波遗留,2026-07-20 决定先观察不设计):当前只做随文预取,增长有界
  (估每日几 MB~十几 MB)。**过期删除与「归档」初衷相抵触**,故不急于加;运维面板「占用空间」读数
  即观察窗口,真需要时从「按源白名单预取 / 老文章降采样压缩 / 容量上限+淘汰」三选。
- ◇ **E 体验波余项**:键盘导航 / 移动端适配(用户表态低优先)。
  含「沉浸阅读模式」——小视口(≤1280)正文行长不足的对症解,替代旧折叠把手方案(2026-07-20 评估结论)。
- ◇ **F 语义搜索入阅读器**(RAG 检索接入用户面)。
- ◇ **Newsletter 三批候补**(见 `docs/archive/source-expansion-wave3-plan.md` 候补名单)。
- ◇ **暗色 / 登录 / 动效三区扩审**(静默仪器重构收官时留下的截图立项项)。
- ◇ **M:Meta AI 源**(httpx 全灭,需浏览器发现;挂起)。

## 已完结(近期,留档索引;执行记录与更早波次见 `docs/archive/README.md`)

- ☑ X 社交波(v3.12.0):X API v2 官方按量付费采集 + 社交媒体第三容器(shape=social,SocialFlow 卡片流);
  6 个白名单账号 preset(全批 incubating;立项 8 个,7-21 压缩成本删 meta/openrouter)+ config 自助加号双路径;引用推/转推扁平化跨平台抽象、真实头像、
  配额守卫与配置面板、零成本本地回填;取缔「今日」容器、隐藏通用源、社交收藏筛选。方案 `docs/social-x-wave-plan.md`。
- ☑ 图床波(v3.11.0):媒体库——正文外链图本地缓存(懒代理 + 随文预取 + 媒体热点图/定点重抓),
  原链不改写、三层降级、防盗链 Referer 解;设计细节见 `CLAUDE.md`「媒体库」节。
- ☑ 文档整理波(2026-07-20):归档已完结方案文档、建三层索引机制(CLAUDE/AGENTS → docs/README → 子 README)。
- ☑ 发现页(v3.10.0)+ 问哆啦美 fab 收缩态(v3.10.1)—— `docs/archive/reader-reshell-plan.md`「发现页轮」。
- ☑ 「接入集成」并入设置柜(v3.9.0)—— 同前档「并入设置轮」。
- ☑ 阅读器样页复刻+双轨合并(v3.6.0)→ 容器化(v3.7.0)→ 导轨靠拢·轨语言统一(v3.8.0)—— 同前档。
- ☑ 源扩容 wave1–3(v3.2.0 → v3.5.0)—— `docs/archive/source-expansion-plan.md` 及 wave3 篇。
- ☑ 静默仪器全站重构(v3.0.0)—— `docs/archive/quiet-instrument-restyle-plan.md`;
  耐久规范沉淀于 `docs/frontend/conventions.md`。
