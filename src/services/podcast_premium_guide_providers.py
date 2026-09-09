"""Concrete provider adapters for the premium-guide neutral ports."""

from __future__ import annotations

import asyncio

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
                        "你是中文科技播客编辑。只依据完整 ASR 文本写精品导读，不做质量评分。"
                        "忽略口语赘词和轻微识别错误，不得补造事实。返回严格JSON，只含字段"
                        "blog_markdown。博客应有标题、导语、核心观点、"
                        "关键案例和结论，保留重要英文专有名词。删除寒暄、重复、广告、跑题和"
                        "不影响结论的细节，产出比逐字稿显著精简的核心内容。"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"节目标题：{title}\n\n完整ASR文本：\n{transcript}\n\n"
                        f"blog_markdown 不超过 {max_chars} 个字符。"
                    ),
                ),
            ],
            config=self._config,
            temperature=0.2,
            response_json=True,
            usage_meta=UsageMeta(purpose="podcast_premium_blog", username=None),
        )
        payload = parse_json_object(raw)
        return PremiumGuideDraft(
            blog_markdown=str(payload["blog_markdown"]),
        )

    async def create_narration(
        self,
        *,
        title: str,
        blog_markdown: str,
        max_chars: int,
        max_minutes: int,
    ) -> str:
        return (
            await chat_completion(
                messages=[
                    ChatMessage(
                        role="system",
                        content=(
                            "你是中文音频编辑。把精品博客改写成可直接朗读的中文导读稿。"
                            "只输出纯文本，不要Markdown、列表符号、舞台说明或SSML。"
                            "开头点明节目主题，中间覆盖最重要的三到五个洞察，结尾给出一句总结。"
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=(
                            f"节目标题：{title}\n\n中文精品博客：\n{blog_markdown}\n\n"
                            f"这是单人速览模式，成品必须不超过 {max_minutes} 分钟，"
                            f"导读稿不超过 {max_chars} 个字符。"
                        ),
                    ),
                ],
                config=self._config,
                temperature=0.2,
                max_tokens=min(self._config.max_tokens, 2400),
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
