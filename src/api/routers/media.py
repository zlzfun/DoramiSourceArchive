"""媒体库（图床）Router：正文外链图片的代理供给与运维管理。

- ``GET /api/media/proxy?url=`` —— 读者面（READER_API_PREFIXES 含 /api/media）：
  命中缓存回本地文件（长缓存头），未命中即时下载后回文件，失败 302 回源
  优雅降级（media 关闭时亦直接 302 回源）。归档正文原链从不改写，显示层
  统一经此端点取图。
- ``GET /api/admin/media/stats`` / ``POST /api/admin/media/backfill`` —— 管理面
  （account_admin_required 匹配 /api/admin 前缀）：缓存统计与存量回填后台 job
  （media_backfill 类型，轮询 GET /api/jobs/{job_id}）。

鉴权仍由 app.py 中间件统一强制；media_store 单例经 _app() 动态读取
（兼容测试 monkeypatch）。
"""

import datetime
import importlib
import logging
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from sqlmodel import Session, func, select

from models.db import ArticleRecord
from services import jobs as jobs_service
from services.media_store import extract_image_urls

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

router = APIRouter(tags=["media"])
logger = logging.getLogger("dorami.media")


def _app():
    """延迟取 api.app（避免导入环 + 兼容测试 monkeypatch media_store/db_sink）。"""
    return importlib.import_module("api.app")


def _redirect_to_origin(url: str) -> RedirectResponse:
    # 失败/停用时的降级：让浏览器直连原图。禁止缓存重定向，
    # 下次访问仍回代理（源站恢复/缓存补全后即切回本地供给）。
    return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/api/media/proxy")
async def media_proxy(url: str = Query(..., description="原始图片 URL")):
    target = (url or "").strip()
    if not target.lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="仅支持 http/https 图片 URL")
    store = _app().media_store
    if store is None:
        return _redirect_to_origin(target)
    record = await store.get_or_fetch(target)
    if record is None:
        return _redirect_to_origin(target)
    path = store.file_path_for(record)
    if not path.is_file():  # 极端竞态（返回后文件被清理）——降级回源
        return _redirect_to_origin(target)
    # 缓存按 URL 内容冻结（归档语义），可长缓存;url_hash 寻址天然免疫参数注入
    return FileResponse(
        path,
        media_type=record.mime or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@router.get("/api/admin/media/stats")
async def media_stats():
    store = _app().media_store
    if store is None:
        return {"enabled": False}
    return {"enabled": True, **store.stats()}


def _require_store():
    store = _app().media_store
    if store is None:
        raise HTTPException(status_code=503, detail="媒体库未启用（[media] enabled = false）")
    return store


def _article_url_statuses(store, rows: List[tuple]) -> Dict[str, List[Dict[str, Any]]]:
    """逐篇提取图链并批量查缓存状态：rows 为 (article_id, content, extensions_json) 列表，
    返回 article_id → [{url, status, error}]。"""
    per_article_urls = {
        article_id: extract_image_urls(content, extensions_json)
        for article_id, content, extensions_json in rows
    }
    all_urls = [u for urls in per_article_urls.values() for u in urls]
    status_map = store.url_status_map(all_urls)
    return {
        article_id: [{"url": u, **status_map[u]} for u in urls]
        for article_id, urls in per_article_urls.items()
    }


@router.get("/api/admin/media/heatmap")
async def media_heatmap(
    days: int = Query(365, ge=1, le=730),
    year: Optional[int] = Query(None, ge=2000, le=2100),
):
    """媒体热点图数据：按文章 fetched_date 逐日聚合图片缓存覆盖情况。

    现算不落表（admin 低频访问，当前归档规模亚秒级）：正文提取图链 →
    按 url_hash 批查 media_assets → cached/failed/pending 三态计数。
    仅返回有文章的日子，空日由前端补格。默认「近 days 天」滚动窗；
    传 year 则取该自然年（年份切换轨），响应恒带 years = 归档覆盖的年份列表（降序）。
    """
    store = _require_store()
    engine = _app().db_sink.engine
    if year is not None:
        cutoff = f"{year}-01-01"
        upper = f"{year + 1}-01-01"
    else:
        cutoff = (datetime.date.today() - datetime.timedelta(days=days - 1)).isoformat()
        upper = None
    with Session(engine) as session:
        query = (
            select(
                ArticleRecord.id,
                ArticleRecord.fetched_date,
                ArticleRecord.content,
                ArticleRecord.extensions_json,
            )
            .where(ArticleRecord.fetched_date >= cutoff)  # ISO 串字典序即时间序
        )
        if upper is not None:
            query = query.where(ArticleRecord.fetched_date < upper)
        articles = session.exec(query).all()
        # 可用年份:最早归档年 → 当前年(降序),供前端年份切换轨;无归档时为空列表。
        earliest = session.exec(select(func.min(ArticleRecord.fetched_date))).one()
        years: List[int] = []
        if earliest:
            first_year = int(str(earliest)[:4])
            this_year = datetime.date.today().year
            years = list(range(this_year, first_year - 1, -1))

    statuses = _article_url_statuses(store, [(a[0], a[2], a[3]) for a in articles])
    day_agg: Dict[str, Dict[str, int]] = defaultdict(
        lambda: {"articles": 0, "with_images": 0, "images_total": 0,
                 "cached": 0, "failed": 0, "pending": 0}
    )
    for article_id, fetched_date, _content, _extensions_json in articles:
        day = (fetched_date or "")[:10]
        if not day:
            continue
        agg = day_agg[day]
        agg["articles"] += 1
        urls = statuses.get(article_id, [])
        if urls:
            agg["with_images"] += 1
            agg["images_total"] += len(urls)
            for item in urls:
                agg[item["status"] if item["status"] in ("cached", "failed") else "pending"] += 1
    return {
        "since": cutoff,
        "year": year,
        "years": years,
        "days": [{"date": day, **agg} for day, agg in sorted(day_agg.items())],
    }


@router.get("/api/admin/media/days/{date}")
async def media_day_detail(date: str):
    """单日明细：当日入库的逐篇文章 + 逐图链缓存状态（热点图格子点击抽屉）。"""
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
    store = _require_store()
    engine = _app().db_sink.engine
    with Session(engine) as session:
        # 只取渲染明细抽屉真正用到的列（id/title/source_id + 提图链的 content/
        # extensions_json），避免把整 ORM 对象连未用列一并拉进内存；已按 date 前缀
        # 过滤，只扫当日行。
        articles = session.exec(
            select(
                ArticleRecord.id,
                ArticleRecord.title,
                ArticleRecord.source_id,
                ArticleRecord.content,
                ArticleRecord.extensions_json,
            )
            .where(ArticleRecord.fetched_date.like(f"{date}%"))
            .order_by(ArticleRecord.fetched_date)
        ).all()
    statuses = _article_url_statuses(
        store, [(a.id, a.content, a.extensions_json) for a in articles]
    )
    payload = []
    for a in articles:
        urls = statuses.get(a.id, [])
        payload.append({
            "id": a.id,
            "title": a.title,
            "source_id": a.source_id,
            "images_total": len(urls),
            "cached": sum(1 for u in urls if u["status"] == "cached"),
            "failed": sum(1 for u in urls if u["status"] == "failed"),
            "pending": sum(1 for u in urls if u["status"] == "pending"),
            "images": urls,
        })
    return {"date": date, "articles": payload}


@router.post("/api/admin/media/articles/{article_id}/prefetch")
async def media_article_prefetch(article_id: str):
    """单篇定点重试：强制（绕过负缓存冷却）预取该文章全部图链，返回刷新后的状态。

    取代全量回填成为存量补录入口——无突发、无反爬压力（一篇至多几张图）。
    """
    store = _require_store()
    app_module = _app()
    engine = app_module.db_sink.engine
    with Session(engine) as session:
        article = session.get(ArticleRecord, article_id)
        if article is None:
            raise HTTPException(status_code=404, detail="文章不存在")
    counts = await store.prefetch_articles(
        [article_id], concurrency=app_module.settings.media.prefetch_concurrency, force=True
    )
    images = _article_url_statuses(
        store, [(article.id, article.content, article.extensions_json)]
    )[article_id]
    return {
        "article_id": article_id,
        "cached": counts["cached"],
        "failed": counts["failed"],
        "images": images,
    }


@router.post("/api/admin/media/backfill")
async def media_backfill():
    """存量回填：扫描全部有正文的文章，把其中的外链图片预取进媒体库。

    提交后台 job 立即返回 {status, job_id}；进度轮询 GET /api/jobs/{job_id}
    （total=文章数，逐篇 advance）。已缓存/负缓存冷却中的 URL 自然跳过。
    """
    app_module = _app()
    store = app_module.media_store
    if store is None:
        raise HTTPException(status_code=503, detail="媒体库未启用（[media] enabled = false）")
    engine = app_module.db_sink.engine
    concurrency = app_module.settings.media.prefetch_concurrency

    async def _work(job: jobs_service.Job) -> dict:
        with Session(engine) as session:
            ids = session.exec(
                select(ArticleRecord.id).where(ArticleRecord.content.is_not(None))
            ).all()
        job.set_total(len(ids))
        cached = failed = with_images = 0
        for article_id in ids:
            with Session(engine) as session:
                row = session.exec(
                    select(ArticleRecord.content, ArticleRecord.extensions_json)
                    .where(ArticleRecord.id == article_id)
                ).first()
            content, extensions_json = row if row else (None, None)
            urls = extract_image_urls(content, extensions_json)
            if urls:
                with_images += 1
                counts = await store.prefetch_urls(urls, concurrency=concurrency)
                cached += counts["cached"]
                failed += counts["failed"]
            job.advance()
        return {
            "articles_scanned": len(ids),
            "articles_with_images": with_images,
            "images_cached": cached,
            "images_failed": failed,
        }

    job = jobs_service.launch(engine, "media_backfill", _work)
    return {"status": "accepted", "job_id": job.id}
