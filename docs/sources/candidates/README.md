# 候选源档案(冷参考)

> 逐厂商/逐板块的候选源调研记录:推荐名单 + Parking Lot + 逐源验证笔记。
> **均为 documentation-only**——收录与否以节点注册表(`src/fetchers/impl/`)与
> `docs/sources/curation_policy.md` 的准入/观察期机制为准,本目录只是选源时的证据库。
> 扩容方法论与读者画像校准(需求侧依据)见 `docs/archive/source-expansion-plan.md` §1.1。

## 状态总览

- **wave1–3 已消化**(v3.2.0–v3.5.0,2026-07):各文档中被采纳的源已建为 preset 节点
  (多数仍在 incubating 观察期,转正评审见 `docs/backlog.md`);未采纳者留在各档 Parking Lot。
- **待启用候补**:`personal_newsletter_sources.md` 的三批候补(Newsletter 长文分析师,
  待二批实践反馈)——backlog 展望层挂账。
- **已裁决关闭**:RSSHub 轨道(H2)、X/微信(H1,前置=账号池)、Meta AI(M,httpx 全灭)
  ——裁决记录在 `docs/archive/source-expansion-wave3-plan.md`。

## 分册索引(按板块)

| 文档 | 覆盖 | 批次 |
|---|---|---|
| `anthropic_claude_sources.md` | Anthropic/Claude 官方渠道 | 初审(06-03) |
| `openai_gpt_codex_sources.md` | OpenAI/GPT/Codex 官方渠道 | 初审(06-03) |
| `google_gemini_antigravity_sources.md` | Google/Gemini 官方渠道(最全一册) | 复审(07-17) |
| `alibaba_qwen_sources.md` / `deepseek_sources.md` / `zhipu_glm_sources.md` / `bytedance_seed_sources.md` / `xai_grok_sources.md` | 国内厂商与 xAI | 初审(06-03) |
| `mistral_sources.md` / `apple_sources.md` / `nvidia_sources.md` | Mistral/Apple/NVIDIA 官方 | wave2/3(07-17/18) |
| `agent_coding_tool_sources.md` | Agent/编程工具(Cursor 等 changelog) | 初审(06-03) |
| `huggingface_platform_sources.md` | HF 平台面(Blog/Daily Papers) | 复审(07-17) |
| `academic_lab_sources.md` | 学术实验室 | 复审(07-17) |
| `tier1_media_community_sources.md` | 媒体/社区/日榜(量子位·HN·Reddit 等,次全一册) | wave3(07-18) |
| `personal_newsletter_sources.md` | 个人博客/Newsletter(**含三批候补**) | wave2(07-17) |
