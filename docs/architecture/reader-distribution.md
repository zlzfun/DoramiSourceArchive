# 读者订阅与交付(feed / MCP / 技能包)

> 状态:耐久参考(快照 2026-10-21)。自 `CLAUDE.md`「Key Design Decisions」**原文迁出**(2026-10-21,文档整理波 issue #210),内容按域归档、未改写;现状以代码为准。本域不变量变化时更新对应节,并在 `docs/version-history.md` 记一行。
> 相关:[`contracts/reader_subscription.md`](../contracts/reader_subscription.md) · [`contracts/feed_delivery.md`](../contracts/feed_delivery.md) · 端点清单 [`reference/endpoints.md`](../reference/endpoints.md)

## Reader subscription & distribution layer

Reader accounts build a personalized subscription scope over already-archived records (it never triggers fetching). One-click subscribe (`POST`/`DELETE /api/reader/sources/{source_id}/subscribe`) creates/removes a per-user, single-source `ReaderSubscriptionRecord` (owned via `owner_username`). "我订阅" = the union of `source_id`s across a user's active subscriptions; for a `user` account it hard-scopes that user's search/QA/MCP retrieval and is the scope of the 阅读器 (the user's primary surface — its 我的订阅 view aggregates subscribed sources via `GET /api/articles?subscribed_scope=only`). Downstream consumers pull via tokens (HMAC-SHA256, stored only as hashes): a per-subscription token (`dsub_`) or the per-user **aggregated feed token** (`dfeed_`, one row per user in `ReaderFeedTokenRecord`) used at `GET /api/public/feed/articles[.md]` — a single endpoint covering all the user's subscribed sources with publish-time/source/type filters. Full contract in `docs/contracts/reader_subscription.md`.

## Downloadable Claude skill

`src/api/skill_router.py` zips `src/skill_templates/dorami-daily-brief/` on the fly (templating `{BASE_URL}` into the live host) and serves it at `GET /api/skill/daily-brief`, so a user can install a ready-made Claude skill that talks to this deployment's feed/MCP endpoints.

## 游客的阅读范围(issue #213)

游客的阅读范围 = 保留账号 `guest:shared` 的订阅行(镜像新账号默认订阅;`feed_service.resolve_subscribed_source_ids` 把访客身份映射过去,隐藏源照旧排除);游客没有 feed / MCP 令牌,不能订阅、改兴趣或签发公开分享(白名单外 403 `login_required`)。总闸开时站内链接「免登录可看」:未登录打开即以游客进入并打开那一篇,分享菜单随之只留站内链接(「复制链接 · 免登录可看」),公开链接区不出现——全站已免登录可读,单篇公开链接的「最小暴露 / 可撤销 / 会过期」不再有意义;总闸关时两档照旧,已发出的公开链接照常有效,整体退役见 issue #215;公开分享页在总闸开且访客没登录账号时页尾多一条「在哆啦美里继续逛逛」(先签游客会话,入口记 `share_page`)。
