"""媒体库（图床）：正文外链图片的本地缓存与代理供给。

设计（2026-07 图床波，推翻早前「外链直连、不代理」决策）：
- **归档正文从不改写**——原始 URL 是档案的一部分；显示层统一经
  ``GET /api/media/proxy?url=`` 取图，本模块负责「URL → 本地缓存文件」。
- 寻址：``url_hash = sha256(url)`` 一行一 URL（MediaAssetRecord）；落盘按
  ``content_hash = sha256(字节)`` 去重（不同 URL 同字节共用一份文件）。
- 供给三径：缓存命中直接回文件；未命中即时下载（懒代理，阶段一）；抓取
  入库后异步预取（急下载，阶段二）+ 存量回填 job——三径共用 get_or_fetch。
- 防护：仅 http/https、解析后所有 IP 必须是公网地址（SSRF）；流式下载带
  字节上限；魔数嗅探确认真是图片（防把 CF 挑战页/错误页缓存成「图」）。
- 失败即负缓存（status=failed + 退避窗口），窗口内不再重试，避免打爆死链。
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import html as html_lib
import ipaddress
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from sqlmodel import Session, select, func

from models.db import ArticleRecord, MediaAssetRecord

logger = logging.getLogger("dorami.media")

# 负缓存退避：失败行在该窗口内不重试（窗口随失败次数线性放大，封顶一天）。
_RETRY_BASE_SECONDS = 6 * 3600
_RETRY_MAX_SECONDS = 24 * 3600

# 正文图链提取：markdown ![alt](url "title") 与内嵌 HTML <img src="...">。
# markdown URL 截断于空白或右括号（title 段自然剥离），支持 <url> 尖括号包裹。
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+[^)]*)?\)")
_HTML_IMAGE_RE = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"']", re.IGNORECASE)

# 魔数嗅探表：防止把非图片响应（HTML 挑战页等）缓存为图。SVG 单独判（文本格式）。
_MAGIC_SNIFF: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF8", "image/gif"),
    (b"BM", "image/bmp"),
    (b"\x00\x00\x01\x00", "image/x-icon"),
)

_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/avif": ".avif",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
    "image/x-icon": ".ico",
    "image/tiff": ".tiff",
}

_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
}


def _referer_for(url: str) -> str:
    """按图片域名推导 Referer——防盗链 CDN 的通用解。

    多数站点把图片放在子域（i.qbitai.com / mmbiz.qpic.cn），校验 Referer 是否
    来自自家站点。取图片域名去掉 i./img./static./cdn./mmbiz 等前缀后的主域作
    Referer（2026-07-20 实测：qbitai 无 Referer 403、带站内 Referer 200——
    该站占当时失败量的大头）。推不出主域时退回图片自身 origin。
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    parts = host.split(".")
    # 三段及以上域名去掉首段子域（i.qbitai.com → qbitai.com），保留 co.uk 类不误伤
    if len(parts) >= 3 and parts[0] in {"i", "img", "imgs", "image", "images", "static", "cdn", "pic", "pics", "media", "assets", "mmbiz"}:
        host = ".".join(parts[1:])
    return f"{parsed.scheme}://{host}/"


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def url_hash_of(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def extract_image_urls(
    content: Optional[str],
    extensions_json: Optional[str | Dict[str, Any]] = None,
) -> List[str]:
    """从正文及扩展字段提取全部 http(s) 图链，保序去重。

    社交帖的正文必须保持纯文本，图片位于 ``media_urls`` 以及
    ``quoted/reposted.media_urls``；这里统一纳入媒体预取，但不把图链反写进正文。
    """
    seen: Dict[str, None] = {}
    if content:
        for match in _MD_IMAGE_RE.finditer(content):
            seen.setdefault(match.group(1).strip(), None)
        for match in _HTML_IMAGE_RE.finditer(content):
            # HTML 属性里的 &amp; 等实体还原成真实 URL
            seen.setdefault(html_lib.unescape(match.group(1)).strip(), None)

    extensions: Dict[str, Any] = {}
    if isinstance(extensions_json, dict):
        extensions = extensions_json
    elif extensions_json:
        try:
            parsed = json.loads(extensions_json)
            if isinstance(parsed, dict):
                extensions = parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    media_groups = [extensions.get("media_urls")]
    for reference_key in ("quoted", "reposted"):
        reference = extensions.get(reference_key)
        if isinstance(reference, dict):
            media_groups.append(reference.get("media_urls"))
    for media_urls in media_groups:
        if isinstance(media_urls, list):
            for url in media_urls:
                if isinstance(url, str):
                    seen.setdefault(url.strip(), None)
    return [u for u in seen if u.lower().startswith(("http://", "https://"))]


def _sniff_image_mime(head: bytes, content_type: str) -> Optional[str]:
    """魔数优先判定图片 MIME；魔数不识时信任 image/* 响应头；都不是则 None。"""
    for magic, mime in _MAGIC_SNIFF:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":  # avif/heic 家族
        return "image/avif"
    stripped = head.lstrip()[:64].lower()
    if stripped.startswith((b"<?xml", b"<svg")):
        return "image/svg+xml"
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized.startswith("image/"):
        return normalized
    return None


# Clash/Surge 等本机代理的 fake-ip DNS 段：开代理时**所有**域名都解析到这里，
# 实际连接经代理隧道出站，并非内网访问——按域名解析时豁免，否则图床整体误杀。
_FAKE_IP_NET = ipaddress.ip_network("198.18.0.0/15")


def _addr_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_loopback
        or addr.is_private
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
    )


async def _resolve_is_public(host: str) -> bool:
    """SSRF 防护：拦截指向本机/内网的目标。

    字面 IP 严格拦截（环回/私网/链路本地等一律拒绝）；域名先解析再查，
    但豁免 fake-ip 段（见 _FAKE_IP_NET）——该段出现即说明本机代理接管了 DNS，
    解析结果不反映真实目标，且危险目标（127.0.0.1/10.x）无法藉此触达。
    """
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        return not _addr_blocked(literal)
    try:
        resolved = await _resolve_host_ips(host)
    except OSError:
        return False
    if not resolved:
        return False
    for raw in resolved:
        try:
            addr = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if addr in _FAKE_IP_NET:
            continue
        if _addr_blocked(addr):
            return False
    return True


async def _resolve_host_ips(host: str) -> List[str]:
    """DNS 解析（独立函数便于测试注入）。"""
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    return [info[4][0] for info in infos]


class SSRFError(ValueError):
    """目标主机指向本机/内网，已按 SSRF 防护拒绝。"""


async def ensure_public_host(host: str) -> None:
    """SSRF 守卫：主机为空或解析后指向本机/内网则抛 SSRFError（中文信息）。

    复用图床下载同款判定（``_resolve_is_public``：字面 IP 严拦、域名解析后查、豁免
    fake-ip 段），供 source_builder 等外部调用方以「拒绝即抛异常」形式复用；图床自身
    下载仍走 ``_resolve_is_public`` 返回布尔的既有分支，行为完全不变。
    """
    if not host or not await _resolve_is_public(host):
        raise SSRFError("目标地址指向本机或内网，已按安全策略拒绝")


class MediaStore:
    """URL → 本地缓存文件 的单一实现（懒代理 / 预取 / 回填三径共用）。"""

    def __init__(
        self,
        engine,
        root: Path,
        *,
        max_bytes: int = 20 * 1024 * 1024,
        timeout_seconds: int = 20,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.engine = engine
        self.root = Path(root)
        self.max_bytes = max_bytes
        self.timeout_seconds = timeout_seconds
        self._transport = transport
        self._client: Optional[httpx.AsyncClient] = None
        # 同一 URL 的并发请求串行化（首个下载，其余命中缓存）
        self._locks: Dict[str, asyncio.Lock] = {}

    # ── 基础设施 ──────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=self.timeout_seconds,
                headers=_DOWNLOAD_HEADERS,
                transport=self._transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    def file_path_for(self, record: MediaAssetRecord) -> Path:
        return self.root / record.content_hash[:2] / f"{record.content_hash}{record.ext}"

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def _get_record(self, url_hash: str) -> Optional[MediaAssetRecord]:
        with Session(self.engine) as session:
            return session.get(MediaAssetRecord, url_hash)

    @staticmethod
    def _retry_window(fail_count: int) -> int:
        return min(_RETRY_BASE_SECONDS * max(fail_count, 1), _RETRY_MAX_SECONDS)

    def _failed_still_cooling(self, record: MediaAssetRecord) -> bool:
        try:
            updated = datetime.datetime.fromisoformat(record.updated_at)
        except (ValueError, TypeError):
            return False
        elapsed = (datetime.datetime.now() - updated).total_seconds()
        return elapsed < self._retry_window(record.fail_count)

    # ── 供给主径 ──────────────────────────────────────────────

    async def get_or_fetch(self, url: str, *, force: bool = False) -> Optional[MediaAssetRecord]:
        """命中缓存或即时下载；成功返回 cached 记录（文件已在盘上），失败 None。

        失败路径会登记/累加负缓存行；冷却窗口内的既有失败直接返回 None 不重试
        （``force=True`` 绕过冷却，供管理面「定点重试」使用）。
        """
        url = (url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return None
        key = url_hash_of(url)

        record = self._get_record(key)
        if record is not None and record.status == "cached":
            if self.file_path_for(record).is_file():
                return record
            # 库有行但盘上文件丢失（手工清理/迁移）→ 当未缓存重下
        if (
            not force
            and record is not None
            and record.status == "failed"
            and self._failed_still_cooling(record)
        ):
            return None

        async with self._lock_for(key):
            # 等锁期间可能已被并发请求补全
            record = self._get_record(key)
            if record is not None and record.status == "cached" and self.file_path_for(record).is_file():
                return record
            return await self._download(url, key)

    async def _download(self, url: str, key: str) -> Optional[MediaAssetRecord]:
        host = urlparse(url).hostname or ""
        if not host or not await _resolve_is_public(host):
            return self._mark_failed(url, key, "非公网主机（SSRF 防护拒绝）")
        try:
            # 带 Referer 请求：防盗链 CDN 无 Referer 会 403（见 _referer_for）；
            # 对不校验的站点无副作用，故统一带上而非失败后重试，省一轮往返。
            headers = {"Referer": _referer_for(url)}
            async with self._get_client().stream("GET", url, headers=headers) as response:
                if response.status_code != 200:
                    return self._mark_failed(url, key, f"HTTP {response.status_code}")
                declared = int(response.headers.get("content-length") or 0)
                if declared > self.max_bytes:
                    return self._mark_failed(url, key, f"超出大小上限（声明 {declared} 字节）")
                chunks: List[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        return self._mark_failed(url, key, "超出大小上限")
                    chunks.append(chunk)
                body = b"".join(chunks)
                mime = _sniff_image_mime(body[:64], response.headers.get("content-type", ""))
                if not body or mime is None:
                    return self._mark_failed(url, key, "响应不是图片")
        except httpx.HTTPError as exc:
            return self._mark_failed(url, key, f"{type(exc).__name__}: {exc}")

        content_hash = hashlib.sha256(body).hexdigest()
        ext = _MIME_EXT.get(mime, ".bin")
        target = self.root / content_hash[:2] / f"{content_hash}{ext}"
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".part")
            tmp.write_bytes(body)
            tmp.replace(target)  # 原子落盘，避免半截文件被当缓存命中

        now = _now()
        with Session(self.engine) as session:
            record = session.get(MediaAssetRecord, key)
            if record is None:
                record = MediaAssetRecord(url_hash=key, url=url, created_at=now)
            record.status = "cached"
            record.content_hash = content_hash
            record.mime = mime
            record.ext = ext
            record.size_bytes = len(body)
            record.last_error = None
            record.fetched_at = now
            record.updated_at = now
            session.add(record)
            session.commit()
            session.refresh(record)
        return record

    def _mark_failed(self, url: str, key: str, reason: str) -> None:
        logger.debug("媒体下载失败 %s: %s", url, reason)
        now = _now()
        with Session(self.engine) as session:
            record = session.get(MediaAssetRecord, key)
            if record is None:
                record = MediaAssetRecord(url_hash=key, url=url, created_at=now)
            record.status = "failed"
            record.fail_count = (record.fail_count or 0) + 1
            record.last_error = reason[:500]
            record.updated_at = now
            session.add(record)
            session.commit()
        return None

    # ── 批量预取（抓取钩子 / 回填 job 共用）──────────────────────

    async def prefetch_urls(
        self, urls: Iterable[str], *, concurrency: int = 4, force: bool = False
    ) -> Dict[str, int]:
        """并发预取一批图链；返回 {cached, failed} 计数（命中既有缓存也算 cached）。"""
        unique = list(dict.fromkeys(u for u in urls if u))
        if not unique:
            return {"cached": 0, "failed": 0}
        semaphore = asyncio.Semaphore(max(concurrency, 1))

        async def _one(target: str) -> bool:
            async with semaphore:
                try:
                    return await self.get_or_fetch(target, force=force) is not None
                except Exception:  # noqa: BLE001 预取绝不让异常外溢到抓取主流程
                    logger.warning("媒体预取异常 %s", target, exc_info=True)
                    return False

        results = await asyncio.gather(*(_one(u) for u in unique))
        cached = sum(1 for ok in results if ok)
        return {"cached": cached, "failed": len(results) - cached}

    async def prefetch_articles(
        self,
        article_ids: Iterable[str],
        *,
        concurrency: int = 4,
        force: bool = False,
    ) -> Dict[str, int]:
        """按文章 ID 批量预取正文图链（抓取入库钩子 / 管理面定点重试共用）。"""
        ids = [i for i in article_ids if i]
        if not ids:
            return {"articles": 0, "cached": 0, "failed": 0}
        with Session(self.engine) as session:
            rows = session.exec(
                select(
                    ArticleRecord.id,
                    ArticleRecord.content,
                    ArticleRecord.extensions_json,
                ).where(ArticleRecord.id.in_(ids))
            ).all()
        urls: List[str] = []
        for _, content, extensions_json in rows:
            urls.extend(extract_image_urls(content, extensions_json))
        counts = await self.prefetch_urls(urls, concurrency=concurrency, force=force)
        return {"articles": len(rows), **counts}

    # ── 状态盘点（管理面热点图用）──────────────────────────────

    def url_status_map(self, urls: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """批量查询图链缓存状态：url → {status: cached/failed/pending, error}。

        无登记行即 pending（从未尝试）；分块 IN 查询避免超长参数列表。
        """
        unique = list(dict.fromkeys(u for u in urls if u))
        result: Dict[str, Dict[str, Any]] = {
            u: {"status": "pending", "error": None} for u in unique
        }
        hash_to_url = {url_hash_of(u): u for u in unique}
        hashes = list(hash_to_url)
        with Session(self.engine) as session:
            for i in range(0, len(hashes), 500):
                chunk = hashes[i:i + 500]
                for record in session.exec(
                    select(MediaAssetRecord).where(MediaAssetRecord.url_hash.in_(chunk))
                ).all():
                    result[hash_to_url[record.url_hash]] = {
                        "status": record.status,
                        "error": record.last_error if record.status == "failed" else None,
                    }
        return result

    # ── 统计 ─────────────────────────────────────────────────

    def stats(self) -> Dict[str, Any]:
        with Session(self.engine) as session:
            cached_count, cached_bytes = session.exec(
                select(func.count(), func.coalesce(func.sum(MediaAssetRecord.size_bytes), 0)).where(
                    MediaAssetRecord.status == "cached"
                )
            ).one()
            failed_count = session.exec(
                select(func.count()).where(MediaAssetRecord.status == "failed")
            ).one()
            # 内容去重后的实际落盘量（不同 URL 同字节共用一份文件）
            per_file = (
                select(
                    MediaAssetRecord.content_hash,
                    func.max(MediaAssetRecord.size_bytes).label("size_bytes"),
                )
                .where(MediaAssetRecord.status == "cached")
                .group_by(MediaAssetRecord.content_hash)
                .subquery()
            )
            distinct_files, disk_bytes = session.exec(
                select(func.count(), func.coalesce(func.sum(per_file.c.size_bytes), 0)).select_from(per_file)
            ).one()
        return {
            "cached_count": int(cached_count or 0),
            "failed_count": int(failed_count or 0),
            "cached_bytes": int(cached_bytes or 0),
            "distinct_files": int(distinct_files or 0),
            "disk_bytes": int(disk_bytes or 0),
        }
