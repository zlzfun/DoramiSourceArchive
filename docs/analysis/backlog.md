# 待办栈(Backlog)

> 性质:**跨波次的待办总账**。各波方案文档(reader-reshell-plan / source-expansion-* / reader-usability-plan 等)
> 内的逐项 ☐ 仍以原文档为准;本文件只维护「波次级」的待办条目与状态,动工时在此标注并链接方案文档。
> 建立于 2026-07-19(用户指示:待办栈落文件)。

## 进行中

- ▶ **管理面应用导轨风格向用户面视图轨靠拢**(用户拍板,2026-07-19 动工;**实施完成,待目检**)
  统一管理面与用户面的左栏风格。实现:复用 `.reader-vrail-*`/`.reader-user-menu` 类族,
  56px icon-only 轨 + 墨底 tooltip + 轨底单一头像菜单;执行记录见
  `docs/analysis/reader-reshell-plan.md`「导轨靠拢轮」。

## 排队中(用户拍板、未动工)

- ☐ **「接入集成」并入设置页的完整设计**
  当前为最小实现(读者账号:头像菜单 → 全屏浮层宿主 MCPTab);完整设计含信息架构与页面归属。
- ☐ **17 个 incubating 源观察期转正评审**
  转正流程见 `docs/analysis/curation_policy.md`「Incubation」节;
  Reddit 转正门槛 = 生产出口 IP 复验 429。
- ☐ **日报源手工名单实践观察**(v3.3.0 落地 `daily_brief_source_ids`,观察实际日报质量后调整名单)

## 展望(用户表态、未立项)

- ◇ **Folo「社交媒体」视图形态**(刷 X 式信息流体验)
  用户明示日后可能推进;与 H1(X/微信源,前置=管理面账号池:凭据池/轮换/健康探测/过期通知)
  和「动态容器信息流化」同脉络。
- ◇ **E 体验波余项**:键盘导航 / 移动端适配(用户表态低优先)。
- ◇ **F 语义搜索入阅读器**(RAG 检索接入用户面)。
- ◇ **Newsletter 三批候补**(见 `docs/analysis/source-expansion-wave3-plan.md` 候补名单)。
- ◇ **暗色 / 登录 / 动效三区扩审**(静默仪器重构收官时留下的截图立项项)。
- ◇ **M:Meta AI 源**(httpx 全灭,需浏览器发现;挂起)。

## 已完结(近期,留档索引)

- ☑ 阅读器样页复刻重构 + 读者态双轨合并(v3.6.0)→ 信息架构容器化(v3.7.0)
  —— 方案与执行记录:`docs/analysis/reader-reshell-plan.md`。
- ☑ 源扩容 wave1–3(v3.2.0 → v3.5.0)—— `docs/analysis/source-expansion-plan.md` 及 wave3 篇。
- ☑ 静默仪器全站重构(v3.0.0)—— `docs/frontend/conventions.md` + 视觉打磨方案文档。
