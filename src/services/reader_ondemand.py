"""读者点播总闸(issue #137):一个开关 + 部署实际能力,决定入口是否露出、端点是否受理。

点播 = 读者按需生成音频,两条链路共用这一个总闸,也共用同一份每日配额池:
- 播客精品导读(``services/podcast_premium_guides``)
- 文章精简旁白(``services/article_listen_guides``)

总闸只表达「要不要对读者开放」。**能不能跑由部署决定**:两条链路都需要 LLM + TTS + 默认
音色就绪,播客侧还需要 Podcast 处理阶段授权——那是内外网部署边界(内网节点不得跑付费
阶段),只认 INI / ``DORAMI_PODCAST_ALLOWED_STAGES``,不由控制台改写。

KV ``reader_ondemand_enabled`` 缺失 = 开(保持既有部署行为),删除该 KV 即回落代码缺省。
管理员在 运维管理 → 内容 → 读者点播 开合;关闭立即生效,已生成的音频与排队记录不动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from sqlmodel import Session

from config import PodcastConfig
from models.db import AppSettingRecord

ENABLED_KEY = "reader_ondemand_enabled"
_TRUE_VALUES = {"1", "true", "yes", "on"}

# 读者点播播客精品导读走的是强制 TTS 流水线(跳过 fetch/asr,复用已有逐字稿),
# 这七个阶段全部授权才有可能跑通;缺一个就不该把入口画给读者。
PODCAST_ONDEMAND_STAGES: tuple[str, ...] = (
    "translate",
    "analyze",
    "digest",
    "script",
    "tts",
    "audio_qa",
    "local_publish",
)

PODCAST_DISABLED_MESSAGE = "当前部署未开启播客点播"
FEATURE_DISABLED_MESSAGE = "点播功能已关闭"


@dataclass(frozen=True)
class OndemandAvailability:
    """一次判定的完整结论:总闸 + 两条链路各自可用性 + 不可用原因(管理面读数)。"""

    enabled: bool
    podcast: bool
    article: bool
    blockers: tuple[str, ...]

    def as_runtime(self) -> dict[str, bool]:
        """读者面只需要两个能力位:入口画不画。"""
        return {"podcast": self.podcast, "article": self.article}


def feature_enabled(session: Session) -> bool:
    """总闸值;KV 缺失按「开」。"""
    record = session.get(AppSettingRecord, ENABLED_KEY)
    if record is None:
        return True
    return str(record.value or "").strip().casefold() in _TRUE_VALUES


def set_feature_enabled(session: Session, enabled: bool) -> None:
    record = session.get(AppSettingRecord, ENABLED_KEY) or AppSettingRecord(key=ENABLED_KEY)
    record.value = "true" if enabled else "false"
    session.add(record)
    session.commit()


def missing_podcast_stages(config: PodcastConfig) -> tuple[str, ...]:
    """本部署缺的点播阶段授权;空元组 = 阶段齐备。"""
    allowed = set(config.allowed_stages)
    return tuple(stage for stage in PODCAST_ONDEMAND_STAGES if stage not in allowed)


def evaluate(
    *,
    switch_on: bool,
    llm_configured: bool,
    tts_configured: bool,
    voice_configured: bool,
    missing_stages: Sequence[str],
) -> OndemandAvailability:
    """纯判定:给定总闸与部署事实,得出两条链路可用性与原因。无 IO,便于穷举测试。"""

    providers_ready = llm_configured and tts_configured and voice_configured
    article = switch_on and providers_ready
    podcast = article and not missing_stages
    blockers: list[str] = []
    if switch_on:
        if not llm_configured:
            blockers.append("LLM 未配置")
        if not tts_configured:
            blockers.append("TTS 未配置")
        if not voice_configured:
            blockers.append("未设默认音色")
        if missing_stages:
            blockers.append("播客处理阶段未授权：" + "、".join(missing_stages))
    return OndemandAvailability(
        enabled=switch_on,
        podcast=podcast,
        article=article,
        blockers=tuple(blockers),
    )


def availability(session: Session, *, podcast_config: PodcastConfig) -> OndemandAvailability:
    """取部署事实后判定;总闸关时短路,不去解析凭据。"""

    if not feature_enabled(session):
        return evaluate(
            switch_on=False,
            llm_configured=False,
            tts_configured=False,
            voice_configured=False,
            missing_stages=(),
        )
    # 延迟导入:本模块被 reader / admin / runtime 三处引用,不把重链路拉进导入期。
    from services import bailian_speech_config, daily_brief

    speech = bailian_speech_config.resolve_config(session)
    return evaluate(
        switch_on=True,
        llm_configured=bool(daily_brief.resolve_llm_config(session).configured),
        tts_configured=bool(getattr(speech, "tts_configured", False)),
        voice_configured=bool((podcast_config.default_voice_profile or "").strip()),
        missing_stages=missing_podcast_stages(podcast_config),
    )
