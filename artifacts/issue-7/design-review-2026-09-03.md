# Issue #7 产品、成本与架构复核（2026-09-03）

## 结论

Issue #7 后续不应直接进入 ASR/TTS 开发。推荐顺序是：

1. 先把现有基于 RSS show notes 的摘要、评分和标签明确标成“简介初评”。
2. 明确 `候选 → 允许处理 → 精品就绪` 三段判定，只有完整逐字稿深评通过后才能称为“精品”。
3. 先完成外网 Artifact Store 与 Archive Bundle v2，再让内网 Reader 消费已发布产物。
4. 用现有 4 个发布者逐字稿单集跑通零 ASR 垂直闭环。
5. 再接批量 ASR 回退；最后接固定中文旁白 TTS。

用户访问单集页只读取和播放已发布结果，不触发 ASR、翻译、摘要或 TTS。

## 1. 当前摘要和标签的真实来源

当前 Podcast fetcher 把 RSS description/show notes 写入 `ArticleRecord.content`。通用文章分析随后读取标题和这段正文：LLM 最多读取前 24,000 字，标签候选召回最多读取前 8,000 字。它不读取 enclosure 音频、`podcast:transcript` 文档或 `extensions_json` 中的播客元数据。

因此当前 35 个单集已有的摘要、质量分和标签全部属于：

```text
标题 + RSS show notes → 简介初评
```

而不是：

```text
音频/发布者全文 → 完整逐字稿 → 全文深度分析
```

校正规则：

- 当前结果统一显示“简介初评 / 基于节目简介”。
- `哆啦美速读` 在 Podcast 中改成“节目简介导读”，不能暗示覆盖全音频。
- 只有分析记录带 `analysis_basis = publisher_transcript | asr_transcript` 和 `input_artifact_hash` 时，才能显示“全文深度分析”。
- 深度摘要和标签同时读取源语言稿、完整中文稿、术语表，证据最终绑定源语言 segment 和原节目时间戳。

## 2. 精品播客的定义

“来源优质”“单集可能值得处理”和“单集已经是精品”是三个不同事实。

### 2.1 状态

| 状态 | 判定依据 | 对外文案 | 是否可花完整 ASR 成本 |
|---|---|---|---:|
| `premium_candidate` | 来源准入、标题、show notes、嘉宾、主题、重复度、历史表现、时长等召回信号，或编辑主动选择 | 候选 | 否 |
| `processing_authorized` | 相关性、权利、输入、预算、抽样/人工批准全部通过 | 待深析 | 是 |
| `premium_ready` | 完整逐字稿 QA 和全文深评通过，精华证据完整 | 精品 / 全文深析 | 已完成 |

### 2.2 硬门

- Podcast 来源已准入；单集由 AI 相关性/初评价值策略命中，或管理员有理由地人工选择。
- 去重通过，音频或发布者逐字稿可用。
- 时长不是精品硬门：`>30 分钟`只可作为长节目标签、候选召回和成本/调度信号；短节目同样可因价值或编辑选择进入处理。
- 转录、翻译、衍生文字、衍生音频和目标 audience 的权利分别允许。
- 月度/单源/单集预算均有余额。
- 有发布者逐字稿则优先使用；无逐字稿时，高置信候选或编辑批准后才允许完整 ASR。

管理端必须为 AI 漏选提供单集级“生成精品播客”入口。人工操作可覆盖模型的相关性/初评价值选择，但不覆盖来源准入、权利、输入安全和预算审计；若将来需要超预算处理，应设计成独立的高权限确认动作并完整留痕。

### 2.3 初始可配置软门

- 元数据相关性 `>= 75`，置信度 `>= 0.80`。
- 元数据初评价值建议 `>= 70`；灰区先做开头/中段/结尾合计 6–10 分钟抽样 ASR。
- 完整逐字稿深度价值 `>= 80`，置信度 `>= 0.80`。
- 深评维度：主题深度 20、信息密度 20、新颖性 15、证据/权威 15、可行动性 15、结构 10、适合精华音频 5。
- 广告占比、重复、标题党、无证据推测、噪声和 ASR 风险单独扣分。

这些是首轮标注集起点，不是永久业务常数；上线前用 30–50 集人工 gold set 校准 Precision@K。

## 3. 中文产物链和质量控制

```text
音频 / 发布者逐字稿
→ 源语言规范稿
→ 分段对齐的完整中文逐字稿
→ 证据事实包
   ├─ 中文精华阅读稿
   └─ 独立中文播音稿 → 固定声线 TTS → 音频 QA
```

- 中文节目：翻译阶段为 no-op，只做标点、分段、显然错误和专名校正。
- 英文节目：保留英文规范稿，再逐 segment 翻译成中文；不能只保存中文而丢失源稿。
- 中英混合：逐段识别语言，中文段清理，英文段翻译，保留相同 segment ID、时间和说话人。
- 读者稿和播音稿共享证据事实包，但互不作为二次改写输入，避免误差层层放大。
- 人物身份只有发布者元数据或编辑证据足够时才落姓名，否则稳定显示说话人 A/B/C。
- 重要主张必须带原节目时间戳；无法验证时降级为“嘉宾观点/不确定表述”或阻止发布。

## 4. 单集页设计

单集页使用一个页面级模式切换：

```text
[ 原节目 · 64:12 ]  [ 中文精华 · 13:06 · AI ]
```

它同时切换播放器、正文默认内容和时间轴，而不是在同页上下堆两个 `<audio>`。

### 原节目模式

- 单一原音频播放器。
- 中文完整稿（默认）、中英对照、原文、章节、Show notes 二级页签。
- 简介初评或全文深析的依据标签。

### 中文精华模式

- 一句话结论、为什么值得听、3–5 个关键观点。
- 带原节目证据时间码的中文精华阅读稿。
- 中文精华音频和音频文字稿；文字已就绪但 TTS 未就绪时仍可阅读。
- 普通用户不显示“现在生成”，只显示已发布、待审核或暂无精华。

### 长逐字稿

- 章节是第一导航单位，segment cursor 是底层加载单位；不一次渲染数万字。
- 同一说话人连续 20–60 秒合成一个可读段，显示时间码、说话人和文本。
- 点击段落跳原音频；点击精华 `[38:24]` 自动切回原节目并定位对应 segment。
- 提供搜索、章节、说话人、中文/对照/原文、自动跟随开关。
- 自动跟随默认关闭，只滚动逐字稿容器；用户手动滚动后暂停跟随。
- URL 保存 `mode/tab/t`，便于刷新、分享和证据深链。
- 原节目与中文精华分别保存进度，页面始终只有一个实际播放器。

该方向结合了 [Apple Podcasts 的可搜索/可跳转逐字稿](https://podcasters.apple.com/support/5316-transcripts-on-apple-podcasts)、[Snipd 的一键 AI DJ 模式](https://www.snipd.com/blog/ai-dj-listen-to-best-parts-of-any-podcast)、[Podwise 的结构化逐字稿](https://docs.podwise.ai/open-api-v1/episodes/get-episode-transcripts)、[NotebookLM 的来源与生成产物分离](https://support.google.com/notebooklm/answer/16212820?hl=en)和 [Readwise Reader 的 Podcast 文档化体验](https://readwise.io/reader/update-dec2025)。本地《欧洲观澜内容聚合平台分析》可作为页面观察笔记，但其中 Whisper、RSS 获取方式和人物识别方式属于推测，不能作为技术事实。

## 5. 预算与供应商结论

公开刊例价（2026-09-03）：

| 方案 | ASR | 4,000 中文汉字 TTS | 60 分钟单集合计 |
|---|---:|---:|---:|
| 阿里 Paraformer-v2 + 腾讯精品音色 | 0.288 元/小时 | 0.120 元 | 0.408 元 |
| 阿里 Paraformer-v2 + 腾讯大模型音色 | 0.288 元/小时 | 0.480 元 | 0.768 元 |
| 阿里 Paraformer-v2 + CosyVoice 3.5 Flash | 0.288 元/小时 | 0.640 元 | 0.928 元 |
| 腾讯大模型 ASR + 腾讯大模型音色 | 0.800 元/小时 | 0.480 元 | 1.280 元 |
| 火山 Seed-ASR + 豆包 TTS | 0.800 元/小时 | 2.000 元 | 2.800 元 |
| 百度普通文件 ASR + 大模型长文本 TTS | 2.000 元/小时 | 1.400 元 | 3.400 元 |

官方依据：[阿里模型价格](https://help.aliyun.com/zh/model-studio/model-pricing)、[阿里 TTS 字符规则](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)、[腾讯 ASR](https://cloud.tencent.com/document/product/1093/35686)、[腾讯 TTS](https://cloud.tencent.com/document/product/1073/34112)、[火山 Seed-ASR](https://www.volcengine.com/docs/6581/2389072?lang=zh)、[百度 ASR](https://ai.baidu.com/ai-doc/SPEECH/Tldjm0i4c)、[百度 TTS](https://ai.baidu.com/ai-doc/SPEECH/Ql9misjot)、[讯飞录音转写](https://www.xfyun.cn/service/lfasr)。

500 小时/日持续 30 天等于 15,000 小时/月。仅阿里最低价批量 ASR 也约 4,320 元/月；加腾讯精品或大模型 TTS 分别约 6,120 元/月、11,520 元/月。因此试运行期 2,000 元额度不能覆盖全部 500 小时/日，但这不构成长期架构约束：100–500 小时/日按弹性输入和峰值吞吐设计，付费处理量由预算漏斗控制，后续提高额度时只调整预算、并发和路由配置。

建议预算漏斗（以每天 500 个、平均一小时节目为压力模型）：

1. 全量只做零 ASR 元数据筛选。
2. 候选节目只做 5 分钟抽样；抽样不得显示为全文摘要。
3. Top 10% 才做完整 ASR、翻译、深评、精华和 TTS。
4. 低置信度/噪声/混语才升级腾讯大模型 ASR。

保守计算：抽样约 360 元/月，Top 10% 完整 ASR 约 432 元/月，Top 10% 腾讯大模型 TTS 约 720 元/月，共约 1,512 元/月；尚未包含 LLM、存储、带宽、失败重试和人工审核。试运行期建议 1,500 元软预警、2,000 元硬上限；额度是配置而非代码常量，后续可提高而无需改流水线。

首测组合：

- 主链：阿里 Paraformer-v2 批量 ASR + 腾讯精品/大模型固定音色盲测。
- 升级链：腾讯大模型 ASR 2.0。
- 并行自建 PoC：中文 FunASR/SenseVoice，英文 faster-whisper/WhisperX，中文 TTS CosyVoice。
- F5-TTS 官方权重为 CC-BY-NC-4.0，不进入商业生产；自建也必须逐项审计代码、权重、voice/训练数据和输出条款。

## 6. 外网计算与内网消费

```text
外网 Collector
  RSS → 简介初评 → 候选/预算/权利闸
  → 发布者逐字稿或 ASR
  → 源语言稿 → 完整中文稿 → 深评/精华/播音稿/TTS
  → 不可变 Artifact Store
  → 发布事务 + Sync Outbox
  → 签名 Archive Bundle v2

内网 Sync Agent
  after_seq 拉 manifest
  → blob 临时下载
  → 签名/hash/bytes/MIME 校验
  → 本地 CAS 原子落盘
  → 单事务 materialize + tombstone
  → 推进 checkpoint

内网 Reader
  只读本地 DB + 本地 Artifact Store
  不调用外部 ASR/LLM/TTS
```

现有 Archive Sync v1 只传 Article JSONL，不传分析、标签和二进制；当前 `JobRecord` 也只是内存任务的 UI 外壳，不是可恢复队列。Bundle v2 必须满足：

- 使用单调 `change_seq` keyset，不使用 offset。
- manifest 任一实体或 blob 失败都不推进 checkpoint。
- 强制 payload checksum 与 manifest 签名。
- blob 内容寻址，先完整校验再原子切换发布指针。
- tombstone/撤权优先同步。
- 内网只保存本地稳定媒体 API，不接受外网 storage URI。
- provider 凭据、工作 lease、未发布草稿和供应商 URL 不进入 bundle。
- HTTP pull 与离线文件包共用同一 importer。

处理 worker 使用数据库 claim、可续期 lease、heartbeat、fencing token、provider task ID 和 stage attempt/cost 记录。现有 `JobRecord` 只聚合批次进度；不能依赖 API 进程里的 `asyncio.create_task` 保证长任务完成。

仍需产品/部署确认一个边界：如果内网终端也不能访问 publisher enclosure URL，原版音频无法播放。必须在“浏览器允许访问原音频域名 / 获权后同步原音频 / 内网只提供 AI 中文精华”中选择一个。

## 7. 当前界面审计

本次在当前 Chromium 暗色主题打开用户指出的 20VC 单集，`reader-ai-summary-text` 计算色为 `rgb(153, 161, 173)`、分析导读为 `rgb(210, 215, 223)`，本机当前版本没有复现纯黑文字。但代码存在跨浏览器/回退风险：

- `:root` 和 `body` 的实际继承色仍是亮色 `#0b1220`；暗色规则只换变量和背景，没有设置根文字色。任何缺少组件级 `color` 的新/旧节点都会在暗底上变黑。
- `::selection` 固定使用 `#0b1220`，暗色选中文字对比不足。
- 仍有组件级硬编码 `#1e293b`，当前依赖后置的暗色特例覆盖，容易遗漏。
- 当前 20VC 详情把 64 分钟节目显示成“阅读时长 3 分钟”，实际只是 show notes 阅读时长，语义误导。
- show notes 中 `$350M ... $1B` 被 Markdown 数学插件误当公式，已在当前页稳定复现。
- Podcast 详情没有显示“简介初评”依据，却把通用摘要显示为“哆啦美速读”，这是比颜色更严重的可信度问题。

建议先建立暗色回归矩阵（Chromium/WebKit/Firefox，桌面/移动）和视觉快照，再统一修复根继承色、selection、硬编码色、Podcast 文案以及美元金额的 Markdown 解析；不要只给 20VC 加局部样式。

当前采集能力的边界也需要明确：Podcast 来源已经能以 `SourceConfigRecord / generic_podcast_rss` 入库并由调度器抓取，Reader 也能发现和播放。复核时发现 35 个共享 Podcast 来源没有进入节点管理，现已把它们逐源接入现有调度板，可查看健康、累计单集、Feed/采集间隔，并执行启停、立即抓取和读者面隐藏。仍未完成的是 Podcast 专用管理面中的准入/权利、最近单集与处理状态，以及单集级“生成精品播客”。

## 8. 重新排序后的交付包

1. **P0.5 事实与同步止血**：初评标签、恢复统一的“发现更多来源”、暗色/Markdown/阅读时长问题、真实 reader 同步调度、失败不推进游标、遗留 job 恢复、内网原音频策略。
2. **P1 准入、采集管理与领域模型**：现有 35 源 shadow review；Podcast 来源管理和单集级人工生成入口；PodcastEpisode、Rights、candidate/authorized 状态；抓取入口统一准入。
3. **P2 Artifact Store + Bundle v2**：CAS、artifact、outbox、manifest、blob、tombstone、内网 importer/materializer。
4. **P3A 零 ASR 垂直闭环**：先用现有 4 个发布者逐字稿单集完成规范稿、中文稿、深评、精华、同步和 Reader 双模式。
5. **P3B ASR 回退**：抽样、完整 ASR、provider 异步状态、lease/heartbeat/fencing、成本账、QA。
6. **P4 中文精华音频**：固定授权声线、结构化播音稿、分段合成、响度/截断/900 秒 QA、预生成后同步。

首个产品验收点应是 P3A 的“文字精品闭环”，而不是先做 TTS。只有文字精华的阅读、证据点击、原节目切回等行为得到验证后，才扩大 TTS 覆盖。
