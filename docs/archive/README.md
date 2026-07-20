# 归档:已完结的方案与执行记录

> 这里是**冷存**:计划已落地或被取代的文档,保留供追溯来龙去脉(为什么这样设计、当时否决了什么)。
> **不要依据本目录判断现状**——现势以 `CLAUDE.md` 架构简报与代码为准;耐久规范已回流至
> `docs/frontend/conventions.md` / `docs/sources/curation_policy.md` 等活跃文档。
> 按「故事」分组索引;文内常含 ☐/☑ 进度符,均为当时状态。

## 阅读器演进(2026-07,v3.2 → v3.10)

- [reader-reshell-plan.md](./reader-reshell-plan.md) — **主档**:按设计样页重构四带式阅读器的五轮记录
  (样页复刻/双轨合并 v3.6 → 容器化 v3.7 → 导轨靠拢·轨语言统一 v3.8 → 接入集成并入设置柜 v3.9 →
  发现页 v3.10),含每轮的用户目检修正与决策理由。
- [reader-usability-plan.md](./reader-usability-plan.md) — 前一波可用性迭代(未读体系/内容形态分流/AI 摘要,B→A→C)。
- [reader-polish-wave2-plan.md](./reader-polish-wave2-plan.md) — 体验二波小项(hover 预取/骨架等)。
- [folo-vs-dorami-reader.md](./folo-vs-dorami-reader.md) — Folo 竞品对照分析(差距定位:阅读循环机制/内容形态学)。

## 源扩容(2026-07,v3.2 → v3.5)

- [source-expansion-plan.md](./source-expansion-plan.md) — 扩容方法论 + wave1(8 文章形 RSS 源)记录;
  §1.1 读者画像校准(daily_brief 兴趣档 + Folo 中文订阅结构)仍被候选文档引用。
- [source-expansion-wave3-plan.md](./source-expansion-wave3-plan.md) — wave2/3 记录(Newsletter 二批、
  官方直连、GitHub Trending 日榜;H1/H2/M 轨道关闭或挂起的裁决);**Newsletter 三批候补名单在此**。

## 静默仪器全站重构(2026-07,v3.0.0)

- [quiet-instrument-restyle-plan.md](./quiet-instrument-restyle-plan.md) — 风格语法 R1–R8 与十四波执行记录
  (布局/台账/运行页/集成/运维/弹窗等)。R 规则的现行版在 `docs/frontend/conventions.md`
  (注意:选中语法在 v3.8 轨语言统一后已演进,以 conventions 为准)。
- [frontend-visual-polish-plan.md](./frontend-visual-polish-plan.md) — 更早的视觉打磨波(A–D/V4/F8-B)。
- [frontend-optimization-plan.md](./frontend-optimization-plan.md) — 最早期前端打磨快照。

## 前端结构重构(2026-07)

- [frontend-refactor-plan.md](./frontend-refactor-plan.md) + [frontend-refactor-progress.md](./frontend-refactor-progress.md)
  — 五阶段前端重构(F1–F8 债务锚点,api.js 拆分、桥接替换等)的计划与进度。

## 后端重构与实体简化(2026-06/07)

- [backend-architecture-review.md](./backend-architecture-review.md) — 触发重构的全面架构评审。
- [backend-refactor-plan.md](./backend-refactor-plan.md) + [backend-refactor-progress.md](./backend-refactor-progress.md)
  + [backend-refactor-report.md](./backend-refactor-report.md) — 五阶段后端重构(路由拆分/Alembic 落地/
  持久化 Job/阶段化)的计划、进度与收官报告。
- [entity-simplification-plan.md](./entity-simplification-plan.md) — 节点组(采集范围)与旧抓取任务的退役方案
  (迁移语义见 CLAUDE.md「Collection Jobs」节与 Alembic `8f6d93196258`)。

## 抓取技术选型(2026-06)

- [crawl4ai-feasibility.md](./crawl4ai-feasibility.md) — crawl4ai 可行性结论(httpx 优先、按需浏览器、可选依赖);
  被 `src/fetchers/web_content/__init__.py` 引为设计原则出处。
- [crawl4ai-migration-nodes.md](./crawl4ai-migration-nodes.md) — B/C 类节点迁移清单与逐节点验证记录。

## 竞品与原理对照

- [horizon-vs-dorami.md](./horizon-vs-dorami.md) — 与 Thysrael/Horizon 的抓取/日报原理对照。
