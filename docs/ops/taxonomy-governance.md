# 标签自动治理启用与验收

> 状态:耐久参考
> 适用于包含 issue #298 / PR #333 的版本。编写时尚未合入、发布或部署；本手册是上线操作说明，不表示目标节点已开启。

本次自动治理负责候选维护、已有名称／别名的精确归并、相关性补标和审核提案维护。
新概念可人工审核，或由管理员逐条发起 AI 审核：置信度严格 >80%、明确建议 new 且无未解决风险时批准为 draft。新同义别名、含糊实体身份与其它关系仍交人工处理；发布是独立操作。
个人自定义兴趣不必等待公共标签发布，也不由这些公共治理开关控制。

**当前部署不接入飞书，也不通知管理员去审核。** 飞书提醒仅保留为默认关闭的灰度能力，不是自动治理的上线前提；站内提案可以独立保留。

## 1. 上线前确认

1. 按[发布流程](release-process.md)部署包含本功能的正式 tag，保留数据库备份；正常入口执行 `ensure_migrated`，不要手工改表或 stamp 跳过迁移。
2. 在公共词表的权威节点启用。双节点保持外网 `[taxonomy] deployment=authority`、内网 `deployment=replica`，与 `[runtime] role` 无关；不要为开启开关把 replica 改成 authority，也不要删除数据库的远端权威标记。
3. 管理员查询 `GET /api/admin/taxonomy/state`：`active_version > 0`。首次安装但未发布时，先完成 [Taxonomy v1 发布流程](taxonomy-v1-deployment.md)；已发布的库不重复首发。
4. 使用正常应用 lifespan 与 APScheduler。仅启动 API 的 `lifespan off` 验收服务不会执行治理任务。
5. 记录启用前的候选、正式标签和补标任务状态，作为启用后的对照；当前不要求飞书接通或真实投递验收。

本期队列、提案和通知记录保留在本机，不新增 Archive Sync 线格式。先升级外网权威节点，内网按既有流程接收公共目录和文章标签；旧内网没有本期审核／私有兴趣界面，不会收到这些本机任务。若内网先升级，仍保持 replica 和下面三个治理开关关闭，不能接管旧外网的词表治理。混版本边界见[方案 §6](../plans/issue-298-interest-governance.md#6-迁移兼容上线与回滚)。

## 2. 哪些开关必须开

| 配置位置 | 键／管理台名称 | 开启后的作用 |
| --- | --- | --- |
| 主 INI `[taxonomy]` | `governance_enabled=true` | 每 5 分钟维护到期候选，刷新统计、轮转存量、恢复过期租约；维护本身不用模型 |
| 主 INI `[taxonomy]` | `exact_merge_enabled=true` | 允许精确归并能力；仍需下面的数据库总开关和已发布目录 |
| 管理台「运维管理 → 分析与标签」 | **精确归并**，存储键 `taxonomy_auto_activation_enabled=true` | 允许实际自动归并唯一已有正式名称／别名，不再表示自动发布新概念 |
| 主 INI `[taxonomy]` | `review_enabled=true`（站内提案可选） | 每 5 分钟生成／更新站内审核提案；模型可用时附语义建议，否则保留未完成语义复核的证据提案；不等于打开通知 |
| 主 INI `[taxonomy]` | `review_ai_enabled=true`（可选灰度） | 允许管理员逐条发起 AI 审核；>80% 的无风险新概念建议批准为草稿，不启动定时或自动发布 |
| 主 INI `[taxonomy]` | `feishu_config_path` **留空** | 当前不接入通知文件，不注册飞书发送任务 |
| 私密 INI `[feishu_notifications]` | `enabled=false`（通知灰度总闸） | 已有私密配置也保持关闭；以后灰度才启用，不需要为本次部署创建文件或填写凭据 |

以上四个主 INI 布尔值和管理台精确归并开关默认关闭。实际自动归并要求 `governance_enabled`、`exact_merge_enabled`、数据库精确归并三个条件同时满足，并且目录已发布、本机有治理权。`review_enabled` 不影响精确归并是否放行。

下面两项是**持续产生新候选证据**的条件，不是维护已有积压的前提：

- **候选证据**：`taxonomy_candidate_enabled=true`，允许文章分析记录公共内容中的未知概念。
- **文章分析**：`article_analysis_enabled=true`，并满足相应源的分析开关、模型配置及正常任务条件。只清理已有候选时无需开启，不要为治理而批量重跑历史全文。

`personal_digest_enabled` 只控制个人早报，不是标签治理的总闸。采集任务、语音任务与已取消的 issue #298 验证自动化也不是启用依赖。

## 3. 权威节点配置与启用顺序

修改实际由 `DORAMI_CONFIG_FILE` 指定的 INI；生产通常为 `config/production.ini`，不要修改 example 文件期待服务生效。

```ini
[taxonomy]
deployment = authority
catalog = config/taxonomy-v1-approved-catalog.json
governance_enabled = true
exact_merge_enabled = true
review_enabled = true
# 需要管理员逐条 AI 审核时才开启，默认关闭。
review_ai_enabled = false
# 当前不接入飞书；通知灰度默认关闭，保持留空。
feishu_config_path =
```

1. 先保留管理台「精确归并」为关闭，通知路径留空（已有飞书私密文件则保持 `enabled=false`），按部署路径受控重启后端加载 INI。文件保存不等于进程已重载；无须再创建系统 cron 或 LaunchAgent。
2. 确认候选维护日志和审核列表可用，再打开管理台「精确归并」。开关灰置时先查服务端能力和节点权威身份，不用数据库写入绕过。
3. 管理台底层使用以下管理员接口；仅发送需要更改的键，其它开关保持原值：

   ```http
   PUT /api/admin/analysis/config
   Content-Type: application/json

   {"taxonomy_auto_activation_enabled": true}
   ```

   使用正常管理员登录会话，随后 `GET /api/admin/analysis/config`，核对 `feature_flags.taxonomy_auto_activation_enabled=true`。不要把 Cookie、密码放进操作记录。
4. 若本次还需要新增候选，在管理台另外打开「候选证据」，按现有分析范围检查「文章分析」及源开关；不一并改动个人早报或采集配置。
5. 自动治理验收不包含接入飞书、试发消息或管理员通知；保持下一节的灰度关闭状态。

内网 replica 保持：

```ini
[taxonomy]
deployment = replica
governance_enabled = false
exact_merge_enabled = false
review_enabled = false
feishu_config_path =
```

### 模型依赖与费用

精确身份归并本身不请求模型，但**只要 `governance_enabled` 或 `review_enabled` 任一为 true，相关性补标 worker 就会注册**。有效模型已配置且存在待办时，它会自动发起核验；关闭管理台「精确归并」不会停止已有补标任务。

- 审核建议与补标核验使用有效主模型配置的 `for_aux()`：配置 `aux_model` 时优先使用该模型，否则沿用主模型；不是个人兴趣专用的 `interest_model`。有效配置包含 INI／环境变量与管理台覆盖，要求 `base_url`、`api_key`、`model` 齐备。
- 审核每轮最多扫描 100 个候选，模型建议每轮最多 8 次调用；补标每轮最多处理 10 篇。它们是批次限制，**不是每日人民币预算**。
- `taxonomy_review_advice` 与 `taxonomy_relevance_retag` 分别记录 AI 用量。没有模型时仍能维护统计、归并身份和整理证据，模型补标不会完成，不能据此声称文章标签已经生效。
- 如果部署设置了 `[llm] paid_authorized_until` 或 `DORAMI_LLM_PAID_AUTHORIZED_UNTIL`，到期／无效时间会阻止新的模型请求。不要复制已经到期的实验配置，也不要以清空期限绕过已有费用授权；正式运行使用本次批准的模型与费用范围。
- 开启前检查已有待办；若要求这一步完全零模型费用，必须在无有效模型的隔离验收环境检查，不能把“关闭文章分析”当成治理补标停费开关。

## 4. 管理员通知：灰度关闭

复用已有 `[feishu_notifications] enabled` 作为通知灰度总闸，默认 `false`；不另加一套重复开关。本次主 INI `feishu_config_path` 留空，不注册发送任务、不创建机器人、不配置 Webhook，也不进行真实试发。

`review_enabled=true` 可以独立生成站内提案；管理员自行打开 `/review/taxonomy` 查看，不会因此收到外部提醒。若连站内提案也暂不需要，可另设 `review_enabled=false`，已有名称的精确归并不受影响。关闭通知不等于自动批准新概念。

只有以后决定在指定部署节点灰度通知时，才同时设置 `review_enabled=true`、可读的 `feishu_config_path` 和私密文件 `enabled=true`，按[飞书通知手册](feishu-review-notifications.md)核对接收目标、路径和发送时机。该开关按部署／通知配置生效，不是按用户百分比分流。

## 5. 启用后的验收

| 检查项 | 入口与预期 |
| --- | --- |
| 开关确实生效 | `GET /api/admin/analysis/config` 读回数据库总开关；`GET /api/admin/taxonomy/state` 同时核对 `active_version > 0` 和 `exact_merge_available=true`。后者仅是能力投影，不能代替数据库总开关检查 |
| 积压有推进 | 后端 `candidate_governance elapsed_ms=… counts=…` 日志每 5 分钟触发；到期候选 `evaluated` 有变化，`failed` 可追查。不要求每轮 `merged > 0`，没有精确同名候选时 0 是正常值 |
| 归并可追溯 | 管理台候选详情对应状态为 merged、目标是已有正式标签；只读核对 `candidate_governance_actions` 的动作、审计事件及补标任务关联。不要为了验收向真实库注入假候选 |
| 补标完成与身份归并分开 | 核对关联 `tag_retag_jobs` 的 `scope_json.relevance_result_counts` 与最终文章标签；assigned、no_match、unknown 分开，queued 不等于完成，未知结果不重置重跑 |
| 提案可处理 | `GET /api/admin/taxonomy/review-proposals?status=pending` 和管理员审核页存在真实证据、建议与修订；新概念仍待审核，approve 后只形成 draft |
| 通知保持关闭 | `feishu_config_path` 为空，已有私密文件 `enabled=false`；不新增真实投递。可读 `GET /api/admin/taxonomy/review-notifications` 对照已有记录，历史 accepted 不代表本次发过消息 |

首轮可能等待约 5 分钟；候选评估后通常隔日复查、无近 30 天证据时降频到 7 天，新增证据或输入变化会唤醒。全量积压不保证一轮清完。需要数据库诊断时只读连接实际目标库，不手工编辑队列、租约、unknown 或通知状态。

常见排查：开关灰置 → 查 INI 和 authority／replica；精确归并始终 0 → 查数据库总闸、已发布目录及是否真的有唯一精确目标；已 merged 但无文章标签 → 查模型、正文／逐字稿、权限和补标结果。当前没有飞书通知是预期状态，不是上线失败。

## 6. 停用与回滚

- **只停自动归并**：管理台关闭「精确归并」，或以同一 PUT 接口写 `taxonomy_auto_activation_enabled=false`。统计、审核提案和已有补标任务仍可继续。
- **只停消息发送**：私密文件设 `enabled=false`；下一次发送入口重新载入文件时生效。已经发出的消息与在途请求不撤回。
- **停止本期公共治理后台任务**：先关闭数据库精确归并，再将主 INI `governance_enabled=false`、`exact_merge_enabled=false`、`review_enabled=false`，私密通知 `enabled=false`，受控重启加载配置。等待在途请求退出并核对记录；不要把它当成停止所有采集／个人兴趣／语音任务的总开关。
- 保留已归并结果、提案、审计和计费记录；关闭开关不撤销历史治理。不删除 unknown 或改身份重付。需要软件回滚时按部署手册切回已验证 tag／备份，不用 Alembic downgrade 伪造业务撤销。

本手册不重新启用已取消的 automation 298、旧 LaunchAgent 或长时收费验证；它们不是正式服务自动治理的组成部分。


## 管理员发起 AI 审核（默认关闭）

主 INI `[taxonomy] review_ai_enabled = true`，受控重启后在“新概念审核”的待审核卡片点击“AI 自动审核”。辅助模型必须已配置；本地权威或 manual 节点可执行，replica／远端治理镜像拒绝。此开关仅允许手动逐条发起，不注册定时任务，不依赖 review_enabled，也不启用飞书。

每条发起持久后台任务，调用辅助模型一次（关闭自动重试），最多提供 3 篇当前允许外发公开文章的标题及 1200 字符正文摘录，以及候选名称和当前目录。置信度为模型自报，并非实测准确率；80% 本身不通过，>80% 仍须是无风险的新概念建议。无效／未知／歧义／同名冲突或输入变更均不自动批准。已处理证据不重复收费，进程中断后可人工处理，不能自动重发。

审批结果、置信度、模型／提示词版本和发起者保存在提案修订记录。通过只创建目录草稿，正式标签数量须发布后才增加；不会自动发布其它草稿。API：POST `/api/admin/taxonomy/review-proposals/{id}/ai-review`，传 expected_revision 与 expected_taxonomy_revision，返回 job_id，经 `/api/jobs/{id}` 查看结果。关掉 review_ai_enabled 会阻止后续审核与在途结果应用，不取消已发送的模型请求。
