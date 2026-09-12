"""Concrete provider adapters for the premium-guide neutral ports."""

from __future__ import annotations

import asyncio

from typing import Any

import httpx

from config import AliyunIsiConfig, LLMConfig
from llm.client import ChatMessage, UsageMeta, chat_completion, parse_json_object
from services.aliyun_isi_tts import AliyunIsiTtsClient, TtsState
from services.podcast_artifacts import sniff_audio_mime
from services.podcast_premium_guides import PremiumGuideDraft, SynthesizedAudio


class OpenAiCompatiblePremiumGuideTextProvider:
    def __init__(self, config: LLMConfig) -> None:
        self._config = config

    async def create_blog(
        self, *, title: str, transcript: str, max_chars: int
    ) -> PremiumGuideDraft:
        raw = await chat_completion(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是中文科技播客深度导读编辑。只依据完整逐字稿/ASR文本撰写深度导读博客，不做质量评分。"
                        "要求：\n"
                        "1. 忽略口语赘词和轻微识别错误，严格基于逐字稿事实，严禁凭空捏造事实、数据或观点。\n"
                        "2. 结构必须包含以下部分（使用清晰的 Markdown 二级/三级标题）：\n"
                        "   - 导语与主题脉络：简述节目的核心主旨、探讨背景与要解决的核心问题。\n"
                        "   - 核心观点与讨论：梳理各发言人的核心主张、观点交锋、互补与共识；观点归属需明确，未明确身份时使用匿名说话人（如说话人A/说话人B），严禁捏造不存在的人物冲突。\n"
                        "   - 关键案例与技术细节：详述播客中涉及的具体案例、技术实现方案、架构选型对比或关键数据支撑。\n"
                        "   - 实践启发与行动建议：提炼面向听众的可落地的行动思考与实践启发。\n"
                        "   - 原音频回听推荐：列出 2-3 处最值得听众去听原声的高光时刻或精彩讨论片段。\n"
                        "3. 保留重要英文专有名词与技术术语。\n"
                        "4. 返回严格 JSON，只包含单一字段 blog_markdown。"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"节目标题：{title}\n\n完整逐字稿：\n{transcript}\n\n"
                        f"请撰写 solo_deep 深度导读博客，blog_markdown 长度严格控制在 {max_chars} 个字符以内。"
                    ),
                ),
            ],
            config=self._config,
            temperature=0.2,
            response_json=True,
            max_tokens=min(self._config.max_tokens, 4000),
            usage_meta=UsageMeta(purpose="podcast_premium_blog", username=None),
        )
        payload = parse_json_object(raw)
        blog_text = str(payload.get("blog_markdown") or "").strip()
        if not blog_text:
            raise ValueError("LLM 返回的 blog_markdown 为空")
        return PremiumGuideDraft(
            blog_markdown=blog_text,
        )

    async def create_narration(
        self,
        *,
        title: str,
        blog_markdown: str,
        max_chars: int,
        max_minutes: int,
        min_minutes: int = 0,
        target_chars: int = 0,
        retry_shorter: bool = False,
        **kwargs: Any,
    ) -> str:
        target_budget_desc = (
            f"目标时长 {min_minutes}–{max_minutes} 分钟，字符预算约 {target_chars or max_chars} 字（硬上限不超过 {max_chars} 字）"
            if min_minutes > 0
            else f"成品必须不超过 {max_minutes} 分钟，导读稿不超过 {max_chars} 个字符"
        )
        retry_instruction = (
            "\n【特别注意】上一版口播稿实测朗读时长偏长，超出目标时长。本次请务必进行精炼紧缩，"
            f"删除次要展开与铺垫，使语言更加紧凑凝练，总字数严格控制在 {max_chars} 字以内！\n"
            if retry_shorter
            else ""
        )

        return (
            await chat_completion(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "你是专业中文音频主播与播客导读编辑。请把深度导读博客改写成可直接用于单人朗读的中文口播稿（solo_deep 单人深度导读稿）。\n"
                            "【严格禁令】\n"
                            "1. 严格禁止直接朗读 Markdown、标题标记（如井号）、列表符号、加粗标记或任何排版符号。\n"
                            "2. 严格禁止输出舞台说明、音效提示、停顿提示（如‘[停顿]’、‘(笑)’）或 SSML 标签。\n"
                            "3. 严格禁止输出任何英文链接、URL 或配图占位符。\n"
                            "【口播要求】\n"
                            "1. 只输出纯净口语文本，语言生动自然，富有感染力。\n"
                            "2. 善用口语过渡词、设问引导和自然的说话语气，把深奥的技术与观点以通俗口吻讲透。\n"
                            "3. 结构完整：开篇自然引导并点明主题背景，主体深入剖析核心洞察与精彩案例细节，结尾给出富有启发的总结收尾。\n"
                            "4. 篇幅严格匹配时长预算（按正常播音语速约 260-280 字/分钟计算，紧扣指定字符预算）。"
                            + retry_instruction
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"节目标题：{title}\n\n中文深度导读博客：\n{blog_markdown}\n\n"
                            f"这是 solo_deep 单人深度导读模式，{target_budget_desc}。"
                            f"请输出纯文本口播稿："
                        ),
                    ),
                ],
                config=self._config,
                temperature=0.2,
                max_tokens=min(self._config.max_tokens, 4000),
                usage_meta=UsageMeta(
                    purpose="podcast_premium_narration", username=None
                ),
            )
        ).strip()


class AliyunIsiPremiumGuideTtsProvider:
    def __init__(
        self,
        config: AliyunIsiConfig,
        *,
        voice_profile: str,
        max_audio_bytes: int,
    ) -> None:
        self._config = config
        self._voice_profile = voice_profile
        self._max_audio_bytes = max_audio_bytes

    async def synthesize(self, text: str) -> SynthesizedAudio:
        client = AliyunIsiTtsClient(self._config)
        try:
            submission = await asyncio.to_thread(
                client.submit, text, voice_profile=self._voice_profile
            )
            deadline = asyncio.get_running_loop().time() + max(
                self._config.tts_provider_deadline_seconds, 600
            )
            while True:
                result = await asyncio.to_thread(client.poll, submission.task_id)
                if result.state is TtsState.SUCCEEDED:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError("TTS 合成超时")
                await asyncio.sleep(self._config.tts_poll_interval_seconds)
        finally:
            client.close()

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as downloader:
            response = await downloader.get(result.audio_url)
            response.raise_for_status()
            data = response.content
        if not data or len(data) > self._max_audio_bytes:
            raise ValueError("TTS 音频为空或超过本地大小限制")
        mime = sniff_audio_mime(data[:64])
        if not mime:
            raise ValueError("TTS 返回了不支持的音频格式")
        return SynthesizedAudio(
            data=data, mime=mime, provider_task_id=submission.task_id
        )


__all__ = [
    "AliyunIsiPremiumGuideTtsProvider",
    "OpenAiCompatiblePremiumGuideTextProvider",
]
