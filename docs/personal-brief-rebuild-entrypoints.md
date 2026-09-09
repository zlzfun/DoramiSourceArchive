# 个人早报重编入口收口——兴趣/订阅变更不再自动触发(issue #33 §5,v3.50.1)

> 状态:**已落地**(2026-09-09)。上接 v3.46 rebuild intent 与 v3.50 重大事件通道;
> 本条是纯收口改动,不引入新表、新旋钮。

## 0. 问题

v3.44 起 `routers/interests.py` 在兴趣写入后直接 `trigger_today_revision(INTEREST_CHANGED)`,
`api/app.py` 中间件又对订阅/自定源的每次写操作 `trigger_today_revision(SUBSCRIPTION_CHANGED)`。
兴趣页连点几个标签(前端虽已 600ms 合并保存)、发现页连订几个源,当日早报就连开几个
revision——版本列表长成流水账,读者感受是「早报自己变了」;编排虽无 LLM 调用,
但每次都要重新冻结范围、重跑选篇并写一整套条目快照。

## 1. 拍板

- **兴趣变更只记录**:`UserInterestTagRecord` 照常写,不触发生成。原样重存(同一套
  stance)不改写 `updated_at`——`personal_digest._interest_version` 把 `updated_at` 混进
  版本哈希,无谓改写会让今日版被误标「关注已更新」。
- **订阅变更同样收口**:订阅/退订/合集批量/自定源增删都不再触发。订阅变的是范围而非偏好,
  但对读者而言同样是「我改了设置,早报什么时候跟上」的问题,两者一个口径最不费解。
- **早报重编只剩两个读者可感知的入口**:手动「重新编排」(`POST /api/reader/briefs/today/rebuild`,
  `MANUAL_REBUILD`)与次日 08:30 定时(`SCHEDULED`),两者都按当时最新的兴趣与订阅编排。
- **系统侧保留两处维护性触发**,不属读者偏好:公共日报就绪追加(`DAILY_BRIEF_READY`,
  把当日公共日报补进已订阅它的读者版面)与管理员下架来源的全员重编
  (`/api/admin/source-visibility/*` → `trigger_all_today_revisions(SUBSCRIPTION_CHANGED)`,
  内容交付层的止损动作,被下架源的条目不该继续挂在任何人的今日版面上)。
- **枚举值保留**:`INTEREST_CHANGED`/`SUBSCRIPTION_CHANGED` 仍在 `DigestGenerationReason`
  与 CHECK 约束里(历史 edition 的 `generation_reason` 在用;服务层 `start_personal_digest_edition`
  仍接受它们,服务层测试沿用),只是读者面不再有新的写入。

## 2. 页面提示:把主动权交给读者

今日端点(`/api/reader/briefs/today`、`/today/ensure`、`/today/rebuild`)的 edition 载荷多两位:

| 字段 | 含义 |
|---|---|
| `interest_stale` | 冻结的 `interest_version` ≠ 读者当前兴趣版本 |
| `scope_stale` | 冻结的 `expected_source_ids` ≠ 当前 `resolve_personal_digest_source_ids` 结果 |

由 `personal_digest.edition_freshness` 计算,只在今日端点开启(历史版本天然落后,
列表端点也不必逐行多查两次)。`PersonalBriefPage` 在今日终态版面上多一行
`.brief-note.is-info`:「你的关注 / 订阅 / 关注和订阅已更新，下次编排生效 · 立即重编」,
行内文字钮直接走既有 `handleRebuild`;编排中或已有排队重编时不画(那两态各有自己的提示行)。
§3「编排说明头」落地时这行并入说明头。

## 3. 边界(有意)

- 今日版面里被退订来源的条目**不会消失**:edition 条目是不可变快照,历史版本本就如此,
  今日版本与之同口径;读者要它跟上就点「立即重编」。
  退订到一个来源都不剩也一样:普通打开(`first_open`)复用今日版本并标 `scope_stale`,
  只有显式重编/定时/系统触发才把当日版本清成 `empty_subscriptions`(codex 检视 P2 返修)。
- 管理员下架来源仍即时重编全员——那是止损不是偏好,且频率极低。
- 前端 `interestVersion` 递增仍会让早报页重拉今日载荷:此前是为了追新编排的版本,现在是为了
  刷新 `interest_stale`,行为不变、语义变了,不另改。

## 4. 验证

- `tests/test_analysis_personal_api.py::test_interest_and_subscription_edits_only_flag_today_edition_stale`:
  关注标签/原样重存/订阅新源三步都不开新 revision、`updated_at` 不被无谓改写、两位标记
  按事实翻转,历史端点不带标记,手动重编开新版且两位归零、范围按最新订阅冻结。
- 既有服务层用例(`tests/test_personal_digest.py` 的 interest_changed/subscription_changed 系列)
  原样通过——服务契约未变。
