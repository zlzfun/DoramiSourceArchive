"""小程序端正文渲染端点(Issue #17):`GET /api/reader/articles/{id}/render`。

- 路径在 `/api/reader` 前缀下,读者门控由 app.py 中间件统一强制;可见性沿
  `routers/articles.load_reader_visible_article`(隐藏源/自定源归属与单条详情同口径)。
- 正文 markdown → 净化 HTML 由 `services/article_render.render_markdown` 完成,图链一律改成
  签名公开图链(`api/media_signing.sign_media_url`),非 http(s) 图丢弃。
- 译文:`extensions_json.translation_zh` 缓存**有效**(无指纹[存量兼容]或指纹与正文一致)时
  同步渲染 `translated_html`;不在此触发翻译(翻译走 `POST /api/reader/ai/translate`)。
- 播客单集附 `podcast` 投影(与列表同源)+ `cover_image`(封面签名链);音频仍是发布者
  enclosure 直链(v3.44「不镜像音频」拍板;域名口径待 P0 实测)。
"""

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Request

from api.articles_view import _podcast_projection
from api.media_signing import sign_media_url
from api.routers.articles import load_reader_visible_article
from services import reader_ai
from services.article_render import render_markdown

router = APIRouter(prefix="/api/reader", tags=["reader"])


def _extensions(record) -> Dict[str, Any]:
    try:
        ext = json.loads(record.extensions_json or "{}")
    except (ValueError, TypeError):
        return {}
    return ext if isinstance(ext, dict) else {}


def _cached_translation(ext: Dict[str, Any], body: str, title: str) -> tuple[Optional[str], Optional[str]]:
    """返回 (译文正文, 译名);任一缓存无效即为 None。判定与 reader_ai.translate_article 同源。"""
    body_fp = reader_ai._body_fingerprint(body)
    if not reader_ai._cache_valid(ext, reader_ai.TRANSLATION_KEY, reader_ai.TRANSLATION_FP_KEY, body_fp):
        return None, None
    translated = str(ext.get(reader_ai.TRANSLATION_KEY) or "")
    translated_title: Optional[str] = None
    if title and reader_ai.looks_chinese(title):
        translated_title = title
    elif reader_ai._cache_valid(
        ext, reader_ai.TRANSLATION_TITLE_KEY, reader_ai.TRANSLATION_TITLE_FP_KEY,
        reader_ai._body_fingerprint(title),
    ):
        translated_title = str(ext.get(reader_ai.TRANSLATION_TITLE_KEY) or "") or None
    return translated, translated_title


@router.get("/articles/{article_id:path}/render")
async def render_article(article_id: str, request: Request):
    record = await load_reader_visible_article(article_id, request)
    ext = _extensions(record)
    title = (record.title or "").strip()
    body = record.content or ""

    html_body, image_urls = render_markdown(body, sign_media_url, title=title)
    translated_body, translated_title = _cached_translation(ext, body.strip(), title)
    translated_html = None
    if translated_body:
        translated_html, _ = render_markdown(translated_body, sign_media_url, title=translated_title or title)

    payload: Dict[str, Any] = {
        "id": record.id,
        "title": title,
        "content_type": record.content_type,
        "source_id": record.source_id,
        "source_url": record.source_url,
        "publish_date": record.publish_date,
        "fetched_date": record.fetched_date,
        "author": ext.get("author_name") or ext.get("author") or "",
        "is_chinese": reader_ai.looks_chinese(f"{title}\n{body[:2000]}"),
        "html": html_body,
        "image_count": len(image_urls),
        "translated_title": translated_title,
        "translated_html": translated_html,
        "has_translation": bool(translated_html),
    }
    if record.content_type == "podcast_episode":
        podcast = _podcast_projection(ext)
        payload["podcast"] = podcast
        payload["cover_image"] = sign_media_url(podcast.get("image_url") or "") or None
    # 社交帖:推文图不在正文里(v3.12 契约),按 media_urls 逐条签名
    media_urls = ext.get("media_urls")
    if isinstance(media_urls, list) and media_urls:
        payload["media_images"] = [
            signed for signed in (sign_media_url(str(u)) for u in media_urls) if signed
        ]
    return payload
