# 整体设计(overview)

> 状态:耐久参考(2026-10-21 首版,文档整理波 issue #210,回应 issue #89「缺整体设计文档」)。本文是地图不是全文:每节末尾链到该域的说明文档;现状以代码为准。

## 1. 一句话

哆啦美把散落在官方博客、媒体、个人博客、X 时间线与播客里的 AI 动态收进同一个 SQLite 归档,交给大模型评分、摘要、打标,再按读者的订阅与兴趣编成每天一份早报;同一份归档经令牌化 feed 与 MCP 交付给下游 Agent。

## 2. 两侧一库

```
采集归档侧                                      读者分发侧
Fetcher ──► DataPipeline ──► DatabaseStorage ◄── 阅读器 / 早报 / 日报 / feed / MCP / 问答
(src/fetchers)  (src/pipeline)   (SQLite + FTS5)      (src/api/routers, frontend/)
                                      ▲
                       分析 worker(评分 / 摘要 / 打标,src/services/article_analysis.py)
```

- **采集归档侧**:`FetcherRegistry` 自动发现 `src/fetchers/impl/` 下的 `BaseFetcher` 子类;每次运行写 `FetchRunRecord` 并更新 `SourceStateRecord`(健康 / 游标)。加源的正道是硬化一个 preset fetcher 并经观察期(`incubating`)转正。→ [`fetchers-and-sources.md`](./fetchers-and-sources.md)、[`../sources/curation_policy.md`](../sources/curation_policy.md)
- **归档**:每条内容带 `content_type`(数据形状)与 `source_id`(渠道)双维身份;子类字段序列化进 `extensions_json`;内容类型 / 事件类型在读取时由规则推导,不落库。检索只有 SQLite FTS5(trigger 同步的影子表)。→ [`content-model.md`](./content-model.md)、[`search-and-qa.md`](./search-and-qa.md)
- **读者分发侧**:读者账号在已归档内容上建订阅范围(永不触发抓取);三个容器 article / social / podcast 形态不同故不混排;兴趣是横切全站的透镜。→ [`reader-distribution.md`](./reader-distribution.md)

## 3. 分析与两条早报产品线

- **文章分析**(v3.44 起):入库后由租约 worker 异步产出新闻价值分(`quality_score`,全站唯一尺子)、内容价值(第二维)、摘要、体裁、规范标签(受治理的 Taxonomy 目录,仓库受控、永不自动发布)与展示标签。缺失即「未分析」,不是零分。
- **个人早报**:按读者订阅范围 ∪ 兴趣命中选篇,冻结范围与兴趣修订,版面快照不可变;重大事件 / 深度阅读 / 播客精选是额外分区;重编入口只剩手动与定时。
- **公共日报**:管理员视角的全站日报,复用同一把评分尺子(缺分就地补评、不写回),三层去重 + 确定性渲染,`extensions.items` 是下游(内网神灯流水线)契约。
- → [`analysis-and-briefs.md`](./analysis-and-briefs.md)、[`llm-and-ai.md`](./llm-and-ai.md)

## 4. 播客与媒体

- 播客单集复用 `ArticleRecord`,只解析 enclosure 元数据不下载音频;精品链路(转录 / 中文精简 / 固定旁白音频)在外网后台预计算,读者访问永不触发付费调用。→ [`podcast.md`](./podcast.md)
- 媒体库缓存正文外链图(原链永不改写,显示层经代理),按内容哈希去重;OSS 持久化与自动备份默认关。→ [`media-and-storage.md`](./media-and-storage.md)

## 5. 角色与权限

- 登录账号角色是主轴:`admin` 超级用户(管理台 + 阅读器双界面),`user` 只读读者;根管理员是平权之上唯一例外(用户级明细只对它可见)。管理写操作全部入审计。外部凭据统一经保管层(只写不回显)。→ [`accounts-and-access.md`](./accounts-and-access.md)
- 游客 `guest`(issue #213):免登录的读者面浏览 + AI 试用;会话按访客身份 `guest:v:<vid>` 记已读与 AI 计次,订阅范围 / 共享早报 / AI 授权落到保留账号 `guest:shared`;可达端点只认白名单 `src/api/guest_policy.py`,其余 403 `login_required`。→ [`accounts-and-access.md`](./accounts-and-access.md)
- `[runtime] role` 是可选第二轴,只在把采集与分发拆到两台机时生效。

## 6. 部署拓扑与同步

- 单节点 `all` 是默认;生产是**外网节点(authority)+ 内网节点(replica)**,两端都 `role=all`,靠拓扑权威而非运行角色分工:外网采集并分析公共源,内网经 Archive Sync 六流(sources → taxonomy → articles → analyses → media → source_states)拉取并服务,内网自定 RSS 本地采集分析。→ [`archive-sync.md`](./archive-sync.md)、[`../ops/aliyun-dual-node-deployment.md`](../ops/aliyun-dual-node-deployment.md)
- 两条官方部署路径:Docker 双容器(推荐,流水线按 tag 部署)与裸机 PM2(运行副本版本化 + 手动回滚)。→ [`../ops/README.md`](../ops/README.md)

## 7. 调度、任务与社交源

- `CollectionJobRecord` 是唯一采集调度实体(一组节点 + 一条 cron);长任务是持久 `JobRecord` + 轮询;调度热重载只差量协调自己的命名空间。→ [`jobs-and-scheduling.md`](./jobs-and-scheduling.md)
- X 时间线经官方 API v2 按量付费采集,一账号一源,配额按返回资源计费。→ [`social-x.md`](./social-x.md)

## 8. 运维面

管理员的运维看板(AI 用量 / 读者活跃 / 内容热度 / 媒体缓存 / 播客处理 / 分析与标签 / 审计),遵守管理面纪律(列头即操作、零说明句、高危入抽屉)。→ [`admin-ops.md`](./admin-ops.md)、[`../frontend/conventions.md`](../frontend/conventions.md)

## 9. 为什么不做 X

被否决或刻意不做的方向(向量 RAG、多维评分、自动回滚、OPML 以外的批量导入……)与再评估触发条件集中在 [`../decisions.md`](../decisions.md);完整论证在 [`../archive/`](../archive/README.md) 的方案文档里。
