"""读者文章点播：精简旁白 → TTS，产物全站共享。

与播客精品导读同构（按需生成、复用不扣次），但：
- 无新闻价值分门槛，有正文即可；
- 正文优先用 translation_zh，否则原文；
- 状态与音频元数据落在 extensions_json.listen_guide；
- 音频字节落本地 CAS（挂在 podcast-artifacts/article-listen 下），不扩 OSS namespace。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import Session

from llm.client import ChatMessage, UsageMeta, chat_completion
from models.db import ArticleRecord
from services.podcast_artifacts import sniff_audio_mime
from services.reader_ai import TRANSLATION_KEY

LISTEN_GUIDE_KEY = "listen_guide"
ACTIVE_STATUSES = frozenset({"queued", "narrating", "synthesizing"})
READY_STATUS = "ready"
FAILED_STATUS = "failed"

# 听感预算：约 6–8 分钟旁白（~260–280 字/分钟）。
MAX_INPUT_CHARS = 12_000
MAX_NARRATION_CHARS = 2_200
MAX_AUDIO_SECONDS = 8 * 60
MAX_AUDIO_BYTES = 20 * 1024 * 1024


class ArticleListenError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class StoredAudio:
    content_hash: str
    mime: str
    size_bytes: int
    duration_seconds: float
    path: Path


class ArticleListenTextProvider(Protocol):
    async def create_narration(
        self, *, title: str, body: str, max_chars: int
    ) -> str: ...


class ArticleListenTtsProvider(Protocol):
    async def synthesize(self, text: str) -> Any: ...


class OpenAiCompatibleArticleListenTextProvider:
    def __init__(self, config) -> None:
        self._config = config

    async def create_narration(
        self, *, title: str, body: str, max_chars: int
    ) -> str:
        raw = await chat_completion(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是中文音频主播与文章导读编辑。请把给定文章改写成可直接朗读的精简旁白口播稿。\n"
                        "【严格禁令】\n"
                        "1. 禁止朗读 Markdown、标题井号、列表符号、加粗标记或任何排版符号。\n"
                        "2. 禁止舞台说明、音效/停顿提示或 SSML。\n"
                        "3. 禁止输出 URL、邮箱或配图占位。\n"
                        "【口播要求】\n"
                        "1. 只输出纯净口语文本，自然、清楚、有节奏。\n"
                        "2. 开篇点题，主体讲清核心论点与关键事实，结尾简短收束。\n"
                        "3. 不编造原文没有的事实、数据或观点；专有名词可保留英文。\n"
                        "4. 篇幅紧扣字符上限（按约 260–280 字/分钟估算听感）。"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=(
                        f"文章标题：{title}\n\n文章正文：\n{body}\n\n"
                        f"请输出精简旁白口播稿，严格不超过 {max_chars} 字："
                    ),
                ),
            ],
            config=self._config,
            temperature=0.2,
            max_tokens=min(self._config.max_tokens, 4000),
            usage_meta=UsageMeta(purpose="article_ondemand_narration", username=None),
        )
        text = (raw or "").strip()
        if not text:
            raise ArticleListenError(
                "article_ondemand_narration_empty",
                "旁白生成失败，请稍后重试",
                status_code=502,
            )
        return text[:max_chars]


class ArticleListenStore:
    """Hash-addressed local audio files for article listen guides."""

    def __init__(self, root: Path, *, max_bytes: int = MAX_AUDIO_BYTES) -> None:
        self.root = Path(root).expanduser().resolve()
        self.max_bytes = max_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, content_hash: str, mime: str) -> Path:
        ext = _ext_for_mime(mime)
        return self.root / content_hash[:2] / f"{content_hash}{ext}"

    def import_bytes(self, data: bytes, *, declared_mime: str) -> StoredAudio:
        if not data:
            raise ArticleListenError(
                "article_ondemand_audio_empty",
                "TTS 返回空音频",
                status_code=502,
            )
        if len(data) > self.max_bytes:
            raise ArticleListenError(
                "article_ondemand_audio_too_large",
                "音频超过本地大小限制",
                status_code=502,
            )
        mime = sniff_audio_mime(data[:64]) or (declared_mime or "").strip().lower()
        if mime not in {"audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac"}:
            raise ArticleListenError(
                "article_ondemand_audio_mime",
                "TTS 返回了不支持的音频格式",
                status_code=502,
            )
        if mime == "audio/x-wav":
            mime = "audio/wav"
        content_hash = hashlib.sha256(data).hexdigest()
        path = self.path_for(content_hash, mime)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            tmp = path.with_suffix(path.suffix + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        duration = _wav_duration_seconds(data) if mime == "audio/wav" else 0.0
        return StoredAudio(
            content_hash=content_hash,
            mime=mime,
            size_bytes=len(data),
            duration_seconds=duration,
            path=path,
        )

    def resolve(self, content_hash: str, mime: str) -> Path | None:
        if not _valid_hash(content_hash):
            return None
        path = self.path_for(content_hash, mime)
        return path if path.is_file() else None


def _valid_hash(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _ext_for_mime(mime: str) -> str:
    return {
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
    }.get(mime, ".bin")


def _wav_duration_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            rate = handle.getframerate()
            if rate <= 0:
                return 0.0
            return handle.getnframes() / float(rate)
    except wave.Error:
        return 0.0


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_extensions(record: ArticleRecord) -> dict[str, Any]:
    try:
        ext = json.loads(record.extensions_json or "{}")
        return ext if isinstance(ext, dict) else {}
    except (TypeError, ValueError):
        return {}


def _guide_of(ext: dict[str, Any]) -> dict[str, Any]:
    raw = ext.get(LISTEN_GUIDE_KEY)
    return raw if isinstance(raw, dict) else {}


def projection_from_extensions(
    article_id: str, extensions: dict[str, Any] | None
) -> dict[str, Any]:
    """Reader-facing listen_guide projection."""
    guide = _guide_of(extensions or {})
    status = str(guide.get("status") or "").strip().lower()
    content_hash = str(guide.get("content_hash") or "").strip().lower()
    mime = str(guide.get("mime") or "").strip().lower()
    ready = status == READY_STATUS and _valid_hash(content_hash) and bool(mime)
    duration = guide.get("duration_seconds")
    try:
        duration_seconds = int(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None
    return {
        "status": status,
        "error": str(guide.get("error") or ""),
        "failed_stage": str(guide.get("failed_stage") or ""),
        "audio_ready": ready,
        "audio_url": (
            f"/api/reader/articles/{article_id}/listen-audio" if ready else ""
        ),
        "duration_seconds": duration_seconds,
    }


def resolve_source_body(record: ArticleRecord) -> tuple[str, str]:
    """Return (body, source_label). Prefer cached ZH translation."""
    ext = _load_extensions(record)
    translated = ext.get(TRANSLATION_KEY)
    if isinstance(translated, str) and translated.strip():
        return translated.strip(), "translation_zh"
    body = (record.content or "").strip()
    return body, "article_body"


def evaluate_reader_ondemand(
    engine,
    *,
    article_id: str,
    actor: str,
) -> dict[str, Any]:
    """Read-only gate before charging quota / scheduling."""
    del actor  # reserved for audit continuity with podcast path
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        if article is None:
            raise ArticleListenError(
                "article_ondemand_not_found",
                "文章不存在",
                status_code=404,
            )
        if article.content_type == "podcast_episode":
            raise ArticleListenError(
                "article_ondemand_podcast_use_podcast_api",
                "播客单集请使用播客点播入口",
                status_code=400,
            )
        body, _source = resolve_source_body(article)
        if not body:
            raise ArticleListenError(
                "article_ondemand_no_body",
                "该文章暂无正文，无法点播",
                status_code=400,
            )
        guide = _guide_of(_load_extensions(article))
        status = str(guide.get("status") or "").strip().lower()
        if status == READY_STATUS and _valid_hash(
            str(guide.get("content_hash") or "").strip().lower()
        ):
            return {"outcome": "ready", "status": READY_STATUS}
        if status in ACTIVE_STATUSES:
            return {"outcome": "in_progress", "status": status}
        return {"outcome": "needs_generation", "status": status or ""}


def prepare_ondemand(engine, *, article_id: str, actor: str) -> dict[str, Any]:
    """Mark queued; return whether a new run should be scheduled."""
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        if article is None:
            raise ArticleListenError(
                "article_ondemand_not_found",
                "文章不存在",
                status_code=404,
            )
        if article.content_type == "podcast_episode":
            raise ArticleListenError(
                "article_ondemand_podcast_use_podcast_api",
                "播客单集请使用播客点播入口",
                status_code=400,
            )
        body, source = resolve_source_body(article)
        if not body:
            raise ArticleListenError(
                "article_ondemand_no_body",
                "该文章暂无正文，无法点播",
                status_code=400,
            )
        ext = _load_extensions(article)
        guide = _guide_of(ext)
        status = str(guide.get("status") or "").strip().lower()
        if status == READY_STATUS and _valid_hash(
            str(guide.get("content_hash") or "").strip().lower()
        ):
            return {
                "outcome": "ready",
                "status": READY_STATUS,
                "should_schedule": False,
            }
        if status in ACTIVE_STATUSES:
            return {
                "outcome": "in_progress",
                "status": status,
                "should_schedule": False,
            }
        guide = {
            "status": "queued",
            "error": "",
            "failed_stage": "",
            "source": source,
            "requested_by": actor,
            "updated_at": _now(),
        }
        ext[LISTEN_GUIDE_KEY] = guide
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.add(article)
        session.commit()
        return {
            "outcome": "queued",
            "status": "queued",
            "should_schedule": True,
        }


def fail_listen_guide(
    engine,
    article_id: str,
    exc: BaseException,
    *,
    failed_stage: str = "",
) -> None:
    message = str(getattr(exc, "message", None) or exc).strip() or type(exc).__name__
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        if article is None:
            return
        ext = _load_extensions(article)
        guide = _guide_of(ext)
        guide.update(
            {
                "status": FAILED_STATUS,
                "error": message[:500],
                "failed_stage": failed_stage or str(guide.get("failed_stage") or ""),
                "updated_at": _now(),
            }
        )
        ext[LISTEN_GUIDE_KEY] = guide
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.add(article)
        session.commit()


def _set_status(
    engine,
    article_id: str,
    status: str,
    *,
    audio: StoredAudio | None = None,
    clear_error: bool = True,
) -> None:
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        if article is None:
            raise ArticleListenError(
                "article_ondemand_not_found",
                "文章不存在",
                status_code=404,
            )
        ext = _load_extensions(article)
        guide = _guide_of(ext)
        guide["status"] = status
        guide["updated_at"] = _now()
        if clear_error:
            guide["error"] = ""
            guide["failed_stage"] = ""
        if audio is not None:
            guide.update(
                {
                    "content_hash": audio.content_hash,
                    "mime": audio.mime,
                    "size_bytes": audio.size_bytes,
                    "duration_seconds": int(round(audio.duration_seconds or 0)),
                }
            )
        ext[LISTEN_GUIDE_KEY] = guide
        article.extensions_json = json.dumps(ext, ensure_ascii=False)
        session.add(article)
        session.commit()


async def run_listen_guide(
    engine,
    store: ArticleListenStore,
    *,
    article_id: str,
    text_provider: ArticleListenTextProvider,
    tts_provider: ArticleListenTtsProvider,
) -> dict[str, Any]:
    try:
        with Session(engine) as session:
            article = session.get(ArticleRecord, article_id)
            if article is None:
                raise ArticleListenError(
                    "article_ondemand_not_found",
                    "文章不存在",
                    status_code=404,
                )
            title = (article.title or "").strip() or "未命名文章"
            body, source = resolve_source_body(article)
            if not body:
                raise ArticleListenError(
                    "article_ondemand_no_body",
                    "该文章暂无正文，无法点播",
                    status_code=400,
                )
            if len(body) > MAX_INPUT_CHARS:
                body = body[:MAX_INPUT_CHARS] + "\n……（正文已截断）"

        _set_status(engine, article_id, "narrating")
        narration = await text_provider.create_narration(
            title=title,
            body=body,
            max_chars=MAX_NARRATION_CHARS,
        )
        _set_status(engine, article_id, "synthesizing")
        synthesized = await tts_provider.synthesize(narration)
        audio = await asyncio.to_thread(
            store.import_bytes,
            synthesized.data,
            declared_mime=getattr(synthesized, "mime", "") or "",
        )
        if audio.duration_seconds and audio.duration_seconds > MAX_AUDIO_SECONDS + 20:
            raise ArticleListenError(
                "article_ondemand_audio_too_long",
                f"旁白音频过长（{audio.duration_seconds:.0f}s），请稍后重试",
                status_code=502,
            )
        _set_status(engine, article_id, READY_STATUS, audio=audio)
        return {
            "article_id": article_id,
            "source": source,
            "content_hash": audio.content_hash,
            "duration_seconds": audio.duration_seconds,
        }
    except Exception as exc:  # noqa: BLE001 - persist then re-raise
        stage = "synthesizing"
        message = str(exc)
        if isinstance(exc, ArticleListenError):
            if "narration" in exc.code:
                stage = "narrating"
        elif "旁白" in message or "narration" in message.lower():
            stage = "narrating"
        fail_listen_guide(engine, article_id, exc, failed_stage=stage)
        raise


def audio_file_for_article(
    engine, store: ArticleListenStore, article_id: str
) -> tuple[Path, str, dict[str, Any]] | None:
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        if article is None:
            return None
        guide = _guide_of(_load_extensions(article))
        if str(guide.get("status") or "").lower() != READY_STATUS:
            return None
        content_hash = str(guide.get("content_hash") or "").strip().lower()
        mime = str(guide.get("mime") or "").strip().lower() or "audio/mpeg"
        path = store.resolve(content_hash, mime)
        if path is None:
            return None
        return path, mime, guide


__all__ = [
    "ACTIVE_STATUSES",
    "ArticleListenError",
    "ArticleListenStore",
    "LISTEN_GUIDE_KEY",
    "MAX_AUDIO_BYTES",
    "MAX_NARRATION_CHARS",
    "OpenAiCompatibleArticleListenTextProvider",
    "audio_file_for_article",
    "evaluate_reader_ondemand",
    "fail_listen_guide",
    "prepare_ondemand",
    "projection_from_extensions",
    "resolve_source_body",
    "run_listen_guide",
]
