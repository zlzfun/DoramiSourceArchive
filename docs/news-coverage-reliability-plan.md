# 新闻覆盖可靠性修复（issue #127）

状态：实现及验证中，未合入、未部署。Issue：<https://github.com/zlzfun/DoramiSourceArchive/issues/127>。
实现方：Codex（GPT-6）；交叉检视：按仓库约定使用本机 Claude Code，实际模型及结论待填写。

## 1. 证据与问题边界

2026-09-21 对生产 3.60.1 进行只读调查。ZCode 9/18 的 IT之家报道已在 9/19 入库，且进入 9/19 **公共日报正文**；管理员个人早报没有选中，属于评分、兴趣与每源配额的结果。9/21 09:02 的道歉开源报道晚于 08:00 抓取及 08:30 日报，不能解释成已经被抓取后丢失。个人兴趣、AIHOT 私有源分析开关及评分提示词保持原有决策。

确有三项独立风险：

1. IT之家只读首页前 18 条，生产一天运行一次。9/20 的部分报道已跌出截取范围，已归档文章也占据名额。
2. 公共日报评分前按每源 15 / 总计 120 截断，但水位推进到全部扫描记录最大时间；未进入评分的文章会被永久跳过。
3. HN 9/17—21 五次运行失败四次。本轮补查发现最后一次重试返回 502，不能靠增加同一路径重试解决；`q=AI` 覆盖不足，且生产公共日报显式名单未含 HN。

公开复核入口：[IT之家原报道](https://www.ithome.com/1/004/310.htm)、[道歉开源](https://www.ithome.com/1/005/046.htm)、[原始技术分析](https://blog.ferstar.org/en/posts/zcode-silent-workspace-snapshot-upload/)。排查证据位于本机主仓 `data/operations/zcode-audit-20260921/`，不含机密的验证结果见下文。

## 2. 逐项实现

### IT之家

使用网页自身的 `category/domainpage` POST 翻页接口，以最后条目的 `data-ot` 毫秒时间为游标。已入库且有正文的文章不占本轮新增额度；已有空正文仍尝试补正文。即使整页均已知，也继续扫描，避免前次限额停在后页后无法恢复。

边界：从首页最新文章时间向前 72 小时，最多 10 页，每轮仍有新增数量上限。短页/空页/窗口终点结束；请求失败、响应异常、重复页或游标不前进、安全页数耗尽均显式失败。预算用尽正常结束，下轮由去重状态继续。它不是无限历史回填器，长时间停摆超过窗口需另行回填。

### 公共日报

新增本节点消费表 `daily_brief_candidates(article_id,status)`，状态为 pending / processed，删除文章时 CASCADE 清理，不参与 Archive Sync。迁移 `b127a6d9e301` 与模型同时提交。

- 新增池受原来源名单及私有源排除规则约束。暂缓候选优先按较早时间消化，再取新增池。
- 每源/总量上限仅限制本次评分预算，超额项保留 pending；成功评分筛选过的候选记 processed。低分、聚类/配额淘汰及评分失败重试后进入附录等原有编辑规则不变。
- 日报正文、候选状态、水位和最近运行数据在一个事务提交。失败全部回滚；dry-run 不消费候选。
- 相同水位时间允许迟到的新 ID 入选，processed 防重复；更早时间戳的事后导入仍需显式回放。
- 同库跨进程互斥，期间变更来源名单或手动游标会使旧生成任务拒绝提交。显式重置/回退游标清空消费表，是有意重放。
- `last_run.candidates_deferred` 显示本轮未处理数；若持续增长，需要提高处理预算或增加手动批次，修复不承诺无限处理吞吐。

已有旧游标之前、旧代码已跳过的候选不会凭空复活；需在验收后有界回放，避免未经确认把整个历史库送给 LLM。

### HN

保留原 hnrss 入口，同时直接取 Algolia 最近 72 小时达最低投票/评论门槛的 stories，最多 8 页 × 100 条，按标题、作者正文及 URL 的 AI/模型/产品品牌词筛选。两入口按 HN discussion GUID 合并，所以已有 ID 不变；已入库的纯发现条目（正文有意为空）也跳过，不消耗新增预算。

RSS 失败仍可由直连补充；两者均失败则运行失败，单入口/后续页失败写降级日志。请求错误保留异常类别和 HTTP 状态。外链帖继续只作发现条目，正文不抓；站内帖保留作者原文。品牌词表不能保证涵盖未来所有新产品。

Algolia 同时是 hnrss 的上游；该路径绕过 RSS 网关，不代表完全独立供应商。公开接口契约：[HN Search API](https://hn.algolia.com/api)、[hnrss 源码](https://github.com/hnrss/hnrss/blob/main/rss.go)。

## 3. 生产配置方案与回滚

`scripts/configure_news_coverage.py` 默认只读，先预览，**部署本 PR 后**再应用：

- 增加专用任务「AI 新闻日间补采」，`15 9-23 * * *`，使用服务时区（生产 UTC+8）；IT之家每轮新增 60，HN 50。保留原 08:00 任务及 08:30 公共日报。
- 仅在已有显式公共日报名单时追加 `rss_hn_ai`；「全部源」状态保持全部。节点开关、旧任务、个人兴趣不变。
- 不自动生成更多收费日报；日间入库供阅读器使用，公共日报次日处理，也可管理员手动生成。
- 默认不开任何生产写入。应用需停止后端及 worker，并传 `--offline`，完成后重启，使调度器加载新任务；拒绝同步接收节点、停用/远端控制源、已被改动的专用任务。
- 一次事务写入，提交前以独占新文件写快照；重复执行不重复建任务。回滚只恢复自身改动，发现配置已被后续编辑则拒绝覆盖。快照只含这项任务与两个日报 KV，不含凭据。

```bash
# 在服务停止前可先只读预览
python3 scripts/configure_news_coverage.py --database data/cms_data.db
# 确认停服、已备份且已部署迁移后应用；snapshot 必须为尚不存在的文件
python3 scripts/configure_news_coverage.py --database data/cms_data.db \
  --apply --offline --snapshot /safe/backups/news-coverage-127.json
# 随后重启服务；回滚同样先停服，操作后重启
python3 scripts/configure_news_coverage.py --database data/cms_data.db \
  --apply --offline --rollback /safe/backups/news-coverage-127.json
```

代码/数据库回滚沿用发布手册的上一 tag + 部署前数据库备份；上面的快照仅撤销调度及名单配置。不要对在线数据库直接写配置后期待调度器自动加载。

验收后如需修复历史遗漏：先预览拟回放时间窗、候选数与 LLM 成本，再在管理面显式回退日报游标并按批次生成。每次事务成功后观察 `candidates_deferred` 直至清空。此操作会重评窗口内已处理文章，应有界执行，不在迁移时自动触发。

## 4. 验证记录

2026-09-21 11:03（UTC+8），真实公开源只读烟测，无生产入库/LLM 调用：

- IT之家 `limit=45`：45 条、45 个唯一 ID，跨过首页 30 条；首条为今天 ZCode 道歉开源报道。
- 模拟 hnrss 故障、Algolia 保持真实：88 条、88 个唯一 ID，包含两条品牌新闻：“ZCode, the GLM coding agent, silently uploads your Git history”（261 分）和 “Inside ZCode: Silently uploading your Git history to the cloud”（331 分）。
- 175 项抓取、日报及端点回归通过；4 项运维配置应用/幂等/回滚/冲突测试通过。
- 全量 pytest、迁移一致性、前端门禁及生产只读配置预览：进行中，完成后补录。

## 5. 交叉检视及上线门禁

按 `CLAUDE.md` 使用本机 Claude Code 只读检视，收到意见先逐条协商，再成批修改、定向复检。结果待补录。

合入前仍需用户本地端到端验收和目检放行；PR 内不改版本，不合入、不发版、不改生产配置。
