# Podcast ASR、中文转录与单旁白 TTS 技术选型

**Issue**: [#7](https://github.com/zlzfun/DoramiSourceArchive/issues/7)
**Decision date**: 2026-09-03
**Status**: Revised after domestic-price/budget review; candidates require benchmark and contract confirmation
**Price basis**: 供应商官方公开价，价格和区域 SKU 上线前必须由账号控制台复核

## 1. 拍板结论

产品产物固定为四层，不能合并：

1. `normalized_transcript`：源语言规范稿，保存原说话人、时间码和原文，是证据源。
2. `transcript_zh`：完整中文转录，逐段映射回源稿；精品播客发布时必须存在。
3. `digest_blog_zh`：有证据引用的中文精华博客，不等同于逐字翻译。
4. `narration_script_zh` / `digest_audio_zh`：由一个固定中文旁白播出的 15 分钟内导读；它是内容压缩稿，不是完整翻译稿的朗读版。

ASR 可以标出 Host/Guest 或 A/B/C，用于回答“谁提出了什么观点”和保留证据；TTS 不复刻这些人的声音。中文成品统一使用 Dorami 固定旁白，优先保证观点、论据、事实、分歧和结论的准确压缩。

供应商决策按部署区域拆开：

| 场景 | 首轮主候选 | 选择理由 | 备选/基准 |
|---|---|---|---|
| 国内试运行主链 | 阿里 Paraformer-v2 批量文件转写；腾讯精品/大模型固定音色 A/B | 公开价最低组合，符合预录播客的批量处理形态；试运行先验证专名、说话人和混语质量 | 腾讯大模型 ASR 2.0 作为低置信升级；CosyVoice 作为中文音质候选 |
| 全球/中英混合链 | AssemblyAI Universal-3.5 Pro + diarization；Azure Neural 单音色分段合成 | 官方明确支持 Mandarin+English code switching；词级时间码、说话人和异步回调齐全；Azure 中文声线/SSML 成熟 | OpenAI/WhisperX 质量基准；Google/AWS 低价旁白 |
| 自托管成本链 | faster-whisper + WhisperX，或 SenseVoice/FunASR；CosyVoice/MeloTTS | 数据可控，稳定大规模时边际成本低 | 只有通过真实吞吐、运维和许可证审计后才切换 |

不能只凭公开单价选唯一供应商。首轮以 10 集真实播客做盲测，满足质量门后才在同档中选便宜者。当前国内主链先验证 **阿里 Paraformer-v2 + 腾讯固定音色**，腾讯大模型 ASR 2.0 作为质量升级；阿里 CosyVoice 和全球链只做同稿盲测/备选。国内输出音频的长期缓存和目标 audience 分发权必须以企业合同确认结果为硬门。

## 2. 内部平台带来的产品修正

对“欧洲观澜”内部分析的可复用观察是产品形态，而不是其中推测的技术栈：它以中文摘要、要点、人物/主题和带说话人标签的完整中文对话稿降低英文长播客门槛。Dorami 应复用这些信息层次，同时增加时间码证据、权利门控、原文对照、处理版本和成本审计。

因此 Issue #7 将以下两项从可选能力改为验收要求：

- 所有获准发布的精品处理结果都有完整中文转录；英文源不能只展示英文逐字稿。
- 15 分钟中文导读采用一个稳定的固定平台旁白；说话人信息保留在逐字稿和引用中，不为了“还原对谈”引入多声线。

## 3. 业界通用流水线

```text
publisher transcript（优先） / source audio
  → 源语言 ASR
  → diarization（谁在何时说）
  → speaker identity mapping（可证实才绑定姓名）
  → 源稿 QA
  → 保持轮次/时间码的完整中文翻译
  → 术语、人名、数字和对齐 QA
  → 证据化摘要/精华博客
  → 单旁白结构化压缩口播稿
  → 按语义段落合成
  → 拼接、响度、静音、截断和 <=900s QA
  → 人工抽检与发布
```

先识别原文、再做文本翻译，是因为音频翻译接口往往会丢失原文证据、专名和时间对齐。例如 OpenAI 的音频 translation 端点当前只输出英文；中文成品必须通过文本翻译层完成。[OpenAI Speech-to-text guide](https://developers.openai.com/api/docs/guides/speech-to-text)

## 4. ASR 托管平台对比

统一以 60 分钟预录音批处理为口径；没有公开价的 diarization 不强行估算。美元和人民币不做汇率混算。

| 平台/模型 | 60 分钟公开价 | 中文/中英混合 | 说话人、时间码、异步 | 判断 |
|---|---:|---|---|---|
| OpenAI `gpt-4o-mini-transcribe` / `gpt-transcribe` / `gpt-4o-transcribe` | $0.18 / $0.27 / $0.36 | 多语言和 code switching 强 | 普通模型不返回 speaker；`gpt-4o-transcribe-diarize` 可返回 speaker+段级起止，但未列独立每分钟估价；单文件 25MB | 适合质量基准或疑难混语补识别，不是当前一站式主链 |
| AssemblyAI Universal-3.5 Pro | $0.21；speaker labels 后 $0.23 | 官方明确 18 种语言原生 code switching，含 Mandarin+English | 词级时间码、speaker、长文件、submit/poll/webhook | 全球中英混合首选 |
| Deepgram Nova-3 | 单语 $0.258；multilingual $0.312 | 中文可单语识别，但 multilingual 清单不含中文 | diarization 含价、词级时间码、callback | 中英混合不合适 |
| Google STT V2 / Chirp 3 | Standard $0.96；Dynamic Batch $0.18 | 支持 zh-CN；多语言识别不等于句内 code-switch | 批处理可 diarization；60 分钟与词级时间码组合需 PoC 核验 | 便宜对照组，不直接定主选 |
| Azure Speech Batch | Southeast Asia S1 约 $0.18 | zh-CN；连续 LID 可跨音频段，官方说明不支持同一句内切换语言 | mono diarization 2–35 人、词级时间码、真正异步 | 纯中文/纯英文低价备选 |
| AWS Transcribe | us-east-1 Tier 1 约 $1.44 | zh-CN；多语言任务能力完整 | 最多 30 speakers、词级时间码、S3 async | 功能完整但价格明显偏高 |
| 阿里 Qwen-Audio 3.0 ASR Filetrans | 北京 ¥0.792；新加坡 ¥0.936 | 中/英/多语、language hints；句内中英混读需实测 | diarization、时间戳、热词、context prompt、2GB/12h async | 国内主选 |
| 阿里 Paraformer-v2 | 约 ¥0.288 | 中/英 hints；混合需实测 | diarization 2–100、时间戳、hotword、async | 成本下界/纯中文备选 |

官方依据：[OpenAI pricing](https://developers.openai.com/api/docs/pricing)、[OpenAI transcription](https://developers.openai.com/api/docs/guides/speech-to-text)、[AssemblyAI pricing](https://www.assemblyai.com/pricing)、[AssemblyAI code switching](https://www.assemblyai.com/blog/universal-3-5-pro-code-switching-contextual-prompting)、[AssemblyAI speaker labels](https://www.assemblyai.com/docs/pre-recorded-audio/label-speakers)、[Deepgram pricing](https://deepgram.com/pricing)、[Deepgram language models](https://developers.deepgram.com/docs/models-languages-overview/)、[Google STT pricing](https://cloud.google.com/speech-to-text/pricing)、[Google Chirp 3](https://docs.cloud.google.com/speech-to-text/docs/models/chirp-3)、[Azure Batch](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/batch-transcription-create)、[Azure language-ID limitation](https://learn.microsoft.com/zh-cn/azure/ai-services/speech-service/language-identification)、[AWS Transcribe pricing](https://aws.amazon.com/transcribe/pricing/)、[Alibaba ASR models](https://help.aliyun.com/zh/model-studio/asr-model/)、[Alibaba model pricing](https://help.aliyun.com/zh/model-studio/model-pricing)。

### 为什么不默认 OpenAI ASR

OpenAI 普通转录价格低且混语能力强，但 Dorami 的发布契约要求 speaker、时间轴和长文件异步处理。普通转录与 diarization 分在不同模型，diarize 模型不支持 prompt，词级时间戳又主要由 `whisper-1` 提供，加上 25MB 分片后会增加跨片 speaker 合并难度。因此它进入 benchmark 和疑难片段 fallback，而不是当前默认的一站式路径。

### 自托管何时更便宜

开源 ASR 的 API 费用接近零，但总成本包含 GPU 空闲、模型加载、说话人模型、对象存储、排队、升级和值班。只有下式在至少两个账期稳定成立，且质量不回退，才从托管切换：

```text
每月自托管固定成本 + 运维成本
< 每月音频小时 × 托管 ASR 每小时价 × (1 + 重试率)
```

首选候选为 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) + [WhisperX](https://github.com/m-bain/whisperX)，中文路线同时评估 [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) / [FunASR](https://github.com/modelscope/FunASR)。代码许可证不能替代模型权重、对齐模型、speaker 模型和训练数据条款审计。

## 5. 中文单旁白 TTS 平台对比

成本样本为 12–14 分钟、约 3,200 个汉字。Azure、阿里等计费规则会把一个中文汉字折算为两个计费字符，表内按各自官方规则估算。单旁白与多旁白的总字符价格原本相近；本次收敛主要降低的是脚本、拼接、声线一致性和验收复杂度。

| 平台/模式 | 样本估算 | 中文旁白实现 | 优点与限制 | 判断 |
|---|---:|---|---|---|
| Azure Neural TTS | 约 $0.096 | 一个 zh-CN 预置音色，按语义段落合成 + SSML + 拼接 | 中文音色、多语声和 SSML 成熟，付费预置音色商用条款清晰 | 全球生产首选 |
| Alibaba CosyVoice v3 Flash / v3.5 Flash | 约 ¥0.64 / ¥0.512 | v3 选一个系统音色；v3.5 需使用审核通过的设计音色 | 中文自然、国内链路、低价；v3.5 无系统音色，成品再分发权需合同确认 | 国内首选 PoC，先测 v3 系统音色 |
| Tencent 长文本 TTS | 约 ¥0.384（按公开通用大模型字符价样本） | 一个预置音色；支持 10 万字符异步 | 公开单价最低、中文长文本工程成熟；正式 SKU 和输出权利需控制台/合同复核 | 国内成本首选候选 |
| Google Neural2 / Chirp 3 HD | $0.0512 / $0.096 | 一个普通话音色分段合成 | Chirp 中文声多但 SSML/长音频部分能力为 Preview | 价格/音质对照组 |
| ElevenLabs Flash / Multilingual | 约 $0.16 / $0.32 | 一个中文旁白 | 表现力强、付费计划可商用；本需求不需要其原生多人能力，成本和数据地域需评审 | 高品质试听组 |
| OpenAI `gpt-4o-mini-tts` | 约 $0.18–0.21/12–14min | 一个内置 voice 分段调用 | 中文受支持、指令控制方便；官方说明内置 voices 主要针对英文优化 | 不作中文默认 |
| AWS Polly Neural / Standard | $0.0512 / $0.0128 | 普通话 `Zhiyu` 单音色 | 单旁白要求下已可用且最便宜，但音色选择和自然度有限 | 低成本基线，不直接定精品默认 |
| Deepgram Aura-2 | 约 $0.096 | 当前官方语种不含中文 | 无法满足中文成品 | 排除 |
| 火山大模型 TTS | 通用大模型样本约 ¥1.60 | 一个中文大模型音色分段合成 | 表现力强，但价格高于腾讯/阿里；原生播客对谈能力不是当前需求 | 国内音质对照组 |
| 科大讯飞长文本配音 | 青岛公开产品口径约 ¥28.16 | 多音色、中英混读 | 当前公开价不具性价比；自定义发音人商用另需申请 | 不进入首轮 |

官方依据：[Azure Speech pricing](https://azure.microsoft.com/en-us/pricing/details/speech/)、[Azure TTS/计费字符](https://learn.microsoft.com/en-us/azure/ai-services/Speech-Service/text-to-speech)、[Azure voice/language support](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)、[Azure paid voice commercial terms](https://www.microsoft.com/licensing/terms/en-US/productoffering/MicrosoftAzureServices/OL/)、[Alibaba model pricing](https://help.aliyun.com/zh/model-studio/model-pricing)、[Alibaba non-realtime TTS](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)、[Tencent TTS pricing/character rules](https://cloud.tencent.com/document/product/1073/34112)、[Tencent long-text TTS](https://cloud.tencent.com/document/api/1073/57373)、[Google TTS pricing](https://cloud.google.com/text-to-speech/pricing)、[Google Gemini TTS](https://docs.cloud.google.com/text-to-speech/docs/gemini-tts)、[Google two-speaker contract](https://docs.cloud.google.com/text-to-speech/docs/reference/rest/Shared.Types/MultiSpeakerVoiceConfig)、[ElevenLabs pricing](https://elevenlabs.io/pricing/api)、[ElevenLabs Text-to-Dialogue](https://elevenlabs.io/docs/overview/capabilities/text-to-dialogue)、[OpenAI TTS guide](https://developers.openai.com/api/docs/guides/text-to-speech)、[OpenAI TTS model](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)、[AWS Polly pricing](https://aws.amazon.com/polly/pricing/)、[AWS Polly voices](https://docs.aws.amazon.com/polly/latest/dg/available-voices.html)、[Deepgram TTS languages](https://developers.deepgram.com/docs/tts-models-languages-overview)、[Volcengine TTS](https://www.volcengine.com/docs/6561/1257543)、[Volcengine pricing example](https://www.volcengine.com/docs/6440/1392584)、[Volcengine Podcast model](https://www.volcengine.com/product/podcast)、[iFlytek long-form TTS pricing](https://qingdao.xfyun.cn/services/online_tts)。

### 为什么默认“单旁白语义分段”

语义分段对 Podcast 产品更可控：

- 每段能绑定证据片段、发音词典、段落类型和预计时长。
- 某一段误读时只重合成该段，成本和延迟低。
- 可以稳定复现、替换供应商并精确控制 900 秒上限。
- 音色下架或权利变化时可换 voice，不重做摘要逻辑。

多声线更适合希望还原对谈或制作娱乐型节目的场景。Dorami 当前目标是高密度导读，加入第二声线不会增加事实或论据，反而引入角色改写和一致性 QA。因此首版不实现多声线；只有 A/B 数据证明它显著提高完播率，才作为展示层实验，不改变源转录和摘要契约。

## 6. 开源 TTS 对比

| 项目 | 许可/能力 | 结论 |
|---|---|---|
| [CosyVoice](https://github.com/QwenAudio/CosyVoice) | 仓库 Apache-2.0；中文、多语、方言、流式和服务化完整 | 中文自托管首选；禁用外部声音克隆，逐权重/音色审计 |
| [MeloTTS](https://github.com/myshell-ai/MeloTTS) | MIT；中文中英混读、CPU 实时 | 低成本基线，但默认中文 speaker 数和长篇听感需实测 |
| [Kokoro](https://github.com/hexgrad/kokoro) | Apache-2.0，轻量；有普通话路线 | 单旁白吞吐基线；中文专名、长文本、G2P 与 voice 权利需核验 |
| ChatTTS / F5-TTS 官方权重 / Fish Speech / XTTS-v2 | 权重非商用、需另签商业许可或输出条款受限 | 不进入生产默认 |

开源生产准入同时审计四件事：代码、模型权重、音色/训练数据、生成输出条款；最严格的一项决定可用边界。

## 7. 成本模型与“性价比最高”的含义

以 60 分钟源节目和约 4,000 个中文汉字的 15 分钟播音稿计算：

| 国内组合 | ASR | TTS | 语音小计/集 |
|---|---:|---:|---:|
| 阿里 Paraformer-v2 + 腾讯精品音色 | ¥0.288 | ¥0.120 | ¥0.408 |
| 阿里 Paraformer-v2 + 腾讯大模型音色 | ¥0.288 | ¥0.480 | ¥0.768 |
| 阿里 Paraformer-v2 + CosyVoice 3.5 Flash | ¥0.288 | ¥0.640 | ¥0.928 |
| 腾讯大模型 ASR 2.0 + 腾讯大模型音色 | ¥0.800 | ¥0.480 | ¥1.280 |
| 火山 Seed-ASR + 豆包 TTS | ¥0.800 | ¥2.000 | ¥2.800 |
| 百度普通文件 ASR + 大模型长文本 TTS | ¥2.000 | ¥1.400 | ¥3.400 |

阿里 CosyVoice 按计费字符收费，一个中文汉字按两个字符；腾讯每个汉字按一个字符，因此不能直接比较标称“每万字符”价格。完整官方链接和 100–500 小时/日预算推演见 [design review](../../artifacts/issue-7/design-review-2026-09-03.md#5-预算与供应商结论)。

试运行期预算为 1,500 元软预警、2,000 元硬上限；额度是配置，后续可提高。每日 100–500 小时是弹性输入与峰值吞吐目标，不要求在当前预算内全量付费处理。控制顺序是：官方稿免 ASR、元数据初筛、灰区 6–10 分钟抽样、只对获授权候选做完整 ASR、只对最终精品后台预生成并缓存 TTS、失败只重做单段。用户访问页面永不触发 TTS。

“性价比最高”定义为单位**可发布成品**成本，而不是 API 标价：

```text
effective_cost =
  API/GPU + retry + storage/egress + engineering/ops
  + 人工纠错分钟 × 编辑分钟成本
```

一个便宜 30% 但 speaker 经常错、专名需要重校的模型，最终通常更贵。

## 8. PoC 与验收

样本至少 10 集：中文 4、英文 3、句内中英混合 3；覆盖双人、3–5 人、抢话、远程连线、广告、数字/模型名和 30–120 分钟时长。

ASR 记录：

- 中文 CER、英文 WER、专名/数字召回率。
- speaker DER、speaker turn 边界误差、真实姓名误绑定次数。
- 时间轴覆盖、失败率、重试率、端到端耗时和实际账单。
- 翻译完整率、源段对齐率、实体/数字一致率。

TTS 记录：

- 20 人盲听 MOS：自然度、中文感、信息清晰度、术语发音、长听疲劳。
- 漏句/重复、段落错序、长静音、截断、响度、实际时长。
- 单段重试率、整集成本、首次可发布率和供应商数据/输出权利。

硬门槛：中文转录覆盖 100%，speaker 映射抽检 >=95%，关键实体/数字一致率 >=98%，成品 <=900 秒，固定声线误用或声音克隆违规为 0。达到硬门槛后，再按 `质量 45% + 人工修正 20% + 成本 20% + 稳定/合规 15%` 选主供应商。

## 9. 实施边界

- Provider adapter 接收源 URI/内容哈希和幂等键，不把供应商响应直接当领域模型。
- `speaker_id`、`speaker_name` 只描述源内容；`voice_id` 只描述成品旁白，分字段保存。
- 翻译与口播稿均保存 prompt/model/glossary/version 和源 segment IDs。
- 每个声线登记 provider、model、voice、许可证据、区域、有效期和 AI disclosure。
- 原始音频不永久镜像；处理临时文件短期删除，衍生音频私有存储并支持撤权。
- 播音稿按语义段落保存证据 manifest，保证每段内容可回溯且可局部重试。
