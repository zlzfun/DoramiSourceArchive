# 标签自动治理启用与验收

> 状态:耐久参考
> 适用于包含 issue #298 / PR #333 的版本。编写时尚未合入、发布或部署；本手册是上线操作说明，不表示目标节点已开启。

本次自动治理负责候选维护、已有名称／别名的精确归并、相关性补标和审核提案维护。
新概念、新同义别名与实体身份仍需管理员审核；审核通过的新标签先成为 draft，发布是独立操作。
个人自定义兴趣不必等待公共标签发布，也不由这些公共治理开关控制。

## 1. 上线前确认

1. 按[发布流程](release-process.md)部署包含本功能的正式 tag，保留数据库备份；正常入口执行 `ensure_migrated`，不要手工改表或 stamp 跳过迁移。
2. 在公共词表的权威节点启用。双节点保持外网 `[taxonomy] deployment=authority`、内网 `deployment=replica`，与 `[runtime] role` 无关；不要为开启开关把 replica 改成 authority，也不要删除数据库的远端权威标记。
3. 管理员查询 `GET /api/admin/taxonomy/state`：`active_version > 0`。首次安装但未发布时，先完成 [Taxonomy v1 发布流程](taxonomy-v1-deployment.md)；已发布的库不重复首发。
4. 使用正常应用 lifespan 与 APScheduler。仅启动 API 的 `lifespan off` 验收服务不会执行治理任务。
5. 记录启用前的候选、正式标签、补标任务与通知状态，作为启用后的对照。

本期队列、提案和通知记录保留在本机，不新增 Archive Sync 线格式。先升级外网权威节点，内网按既有流程接收公共目录和文章标签；旧内网没有本期审核／私有兴趣界面，不会收到这些本机任务。若内网先升级，仍保持 replica 和下面三个治理开关关闭，不能接管旧外网的词表治理。混版本边界见[方案 §6](../plans/issue-298-interest-governance.md#6-迁移兼容上线与回滚)。

## 2. 哪些开关必须开

| 配置位置 | 键／管理台名称 | 开启后的作用 |
| --- | --- | --- |
| 主 INI `[taxonomy]` | `governance_enabled=true` | 每 5 分钟维护到期候选，刷新统计、轮转存量、恢复过期租约；维护本身不用模型 |
| 主 INI `[taxonomy]` | `exact_merge_enabled=true` | 允许精确归并能力；仍需下面的数据库总开关和已发布目录 |
| 管理台「运维管理 → 分析与标签」 | **精确归并**，存储键 `taxonomy_auto_activation_enabled=true` | 允许实际自动归并唯一已有正式名称／别名，不再表示自动发布新概念 |
| 主 INI `[taxonomy]` | `review_enabled=true` | 每 5 分钟生成／更新审核提案；模型可用时附语义建议，否则保留未完成语义复核的证据提案 |
| 主 INI `[taxonomy]` | `feishu_config_path` | 指定服务进程可读取的私密通知文件；与 `review_enabled` 一起控制发送任务注册 |
| 私密 INI `[feishu_notifications]` | `enabled=true` | 实际允许飞书投递；只写路径或只打开 `review_enabled` 不会绕过此总闸 |

以上三个主 INI 布尔值和管理台精确归并开关默认关闭。实际自动归并要求 `governance_enabled`、`exact_merge_enabled`、数据库精确归并三个条件同时满足，并且目录已发布、本机有治理权。`review_enabled` 不影响精确归并是否放行。

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
# 暂不发送飞书时留空；接入步骤见下一节。
feishu_config_path =
```

1. 先保留管理台「精确归并」为关闭、飞书私密文件 `enabled=false`，按部署路径受控重启后端加载 INI。文件保存不等于进程已重载；无须再创建系统 cron 或 LaunchAgent。
2. 确认候选维护日志和审核列表可用，再打开管理台「精确归并」。开关灰置时先查服务端能力和节点权威身份，不用数据库写入绕过。
3. 管理台底层使用以下管理员接口；仅发送需要更改的键，其它开关保持原值：

   ```http
   PUT /api/admin/analysis/config
   Content-Type: application/json

   {"taxonomy_auto_activation_enabled": true}
   ```

   使用正常管理员登录会话，随后 `GET /api/admin/analysis/config`，核对 `feature_flags.taxonomy_auto_activation_enabled=true`。不要把 Cookie、密码放进操作记录。
4. 若本次还需要新增候选，在管理台另外打开「候选证据」，按现有分析范围检查「文章分析」及源开关；不一并改动个人早报或采集配置。
5. 需要通知时按下一节接通并启用飞书。

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

## 4. 飞书审核提醒

私密文件配置、签名和错误语义见[飞书通知手册](feishu-review-notifications.md)。最小启用形态：

```ini
[feishu_notifications]
enabled = true
webhook_url = <已配置目标群的真实 Webhook>
signing_secret = <该机器人的签名密钥>
review_base_url = https://<接收人可访问的 Dorami 域名>
timezone = Asia/Shanghai
digest_hour = 18
timeout_seconds = 15
allow_loopback_review_url = false
```

替换占位符后保存为服务账户可读的私密文件（权限 `600`），将其**服务进程视角的绝对路径**写入主 INI 的 `feishu_config_path`，重新加载后端。Docker 默认只挂载生产 INI 单文件与 data 目录，不会自动挂载宿主任意私密路径：例如宿主部署目录下 `data/private/feishu-review.ini` 对应容器内 `/app/data/private/feishu-review.ini`；按敏感数据管理该文件及备份。裸机使用本机服务账户可读的绝对路径。凭据不提交 Git。

收件人需能打开 `/review/taxonomy` 和 `/review/taxonomy/<id>` 并以管理员登录；`review_base_url` 只填站点 origin，不拼上述路径、query、fragment 或 token。先核对该入口可达，再把私密文件 `enabled` 改为 true。

发送主触发固定为**北京时间 18:00**，不依赖宿主时区；另有每 5 分钟的补偿触发，同样受当天时刻、批次状态和私密文件总闸约束。18:00 之后才启用时，首个补偿轮次可能发送当天积压，不一定等到明天。没有有效更新不发；一个日期最多一个逻辑批次，明确失败最多尝试 3 次，超时／中断 `unknown` 不盲重发。`accepted` 仅表示飞书接收，不是用户已读或已审核。

## 5. 启用后的验收

| 检查项 | 入口与预期 |
| --- | --- |
| 开关确实生效 | `GET /api/admin/analysis/config` 读回数据库总开关；`GET /api/admin/taxonomy/state` 同时核对 `active_version > 0` 和 `exact_merge_available=true`。后者仅是能力投影，不能代替数据库总开关检查 |
| 积压有推进 | 后端 `candidate_governance elapsed_ms=… counts=…` 日志每 5 分钟触发；到期候选 `evaluated` 有变化，`failed` 可追查。不要求每轮 `merged > 0`，没有精确同名候选时 0 是正常值 |
| 归并可追溯 | 管理台候选详情对应状态为 merged、目标是已有正式标签；只读核对 `candidate_governance_actions` 的动作、审计事件及补标任务关联。不要为了验收向真实库注入假候选 |
| 补标完成与身份归并分开 | 核对关联 `tag_retag_jobs` 的 `scope_json.relevance_result_counts` 与最终文章标签；assigned、no_match、unknown 分开，queued 不等于完成，未知结果不重置重跑 |
| 提案可处理 | `GET /api/admin/taxonomy/review-proposals?status=pending` 和管理员审核页存在真实证据、建议与修订；新概念仍待审核，approve 后只形成 draft |
| 通知可对账 | `GET /api/admin/taxonomy/review-notifications` 检查批次状态；首次真实投递同时核对目标群和审核链接，不能只凭 HTTP 200 宣称收件人看到 |

首轮可能等待约 5 分钟；候选评估后通常隔日复查、无近 30 天证据时降频到 7 天，新增证据或输入变化会唤醒。全量积压不保证一轮清完。需要数据库诊断时只读连接实际目标库，不手工编辑队列、租约、unknown 或通知状态。

常见排查：开关灰置 → 查 INI 和 authority／replica；精确归并始终 0 → 查数据库总闸、已发布目录及是否真的有唯一精确目标；已 merged 但无文章标签 → 查模型、正文／逐字稿、权限和补标结果；18:00 没通知 → 查私密路径／总闸、待审核更新、当日批次状态与接收地址。

## 6. 停用与回滚

- **只停自动归并**：管理台关闭「精确归并」，或以同一 PUT 接口写 `taxonomy_auto_activation_enabled=false`。统计、审核提案和已有补标任务仍可继续。
- **只停消息发送**：私密文件设 `enabled=false`；下一次发送入口重新载入文件时生效。已经发出的消息与在途请求不撤回。
- **停止本期公共治理后台任务**：先关闭数据库精确归并，再将主 INI `governance_enabled=false`、`exact_merge_enabled=false`、`review_enabled=false`，私密通知 `enabled=false`，受控重启加载配置。等待在途请求退出并核对记录；不要把它当成停止所有采集／个人兴趣／语音任务的总开关。
- 保留已归并结果、提案、审计和计费记录；关闭开关不撤销历史治理。不删除 unknown 或改身份重付。需要软件回滚时按部署手册切回已验证 tag／备份，不用 Alembic downgrade 伪造业务撤销。

本手册不重新启用已取消的 automation 298、旧 LaunchAgent 或长时收费验证；它们不是正式服务自动治理的组成部分。
