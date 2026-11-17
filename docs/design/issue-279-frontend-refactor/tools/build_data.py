"""把 2026-11-10 生产游客接口快照(只读)裁剪成样页用的 assets/data.js。

用法: python build_data.py <api 快照目录> <仓库根>
不做人工筛选或改写;裁掉的只是样页用不到的字段。图片与徽标落到 assets/img/ 或内嵌 data URI。
"""
import base64, glob, hashlib, io, json, os, re, subprocess, sys, urllib.parse

API, REPO = sys.argv[1], sys.argv[2]
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets")
IMG = os.path.join(OUT, "img")
os.makedirs(IMG, exist_ok=True)
from PIL import Image

def J(name):
    p = os.path.join(API, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None

def fetch(url, timeout=25):
    try:
        r = subprocess.run(["curl", "-sL", "-m", str(timeout), "-A", "Mozilla/5.0", url], capture_output=True)
        return r.stdout if r.returncode == 0 and len(r.stdout) > 100 else None
    except Exception:
        return None

def to_data_uri(raw, size):
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGBA")
        im.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO(); im.save(buf, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None

CACHE = os.path.join(API, "_cache"); os.makedirs(CACHE, exist_ok=True)
def cached(url):
    fn = os.path.join(CACHE, hashlib.sha1(url.encode()).hexdigest())
    if os.path.exists(fn):
        return open(fn, "rb").read() or None
    raw = fetch(url) or b""
    open(fn, "wb").write(raw)
    return raw or None

# ── 来源:角色(移植 utils/sourceRole.js)+ 公司徽标(移植 sourceTaxonomy.js 的注册表口径) ──
PERSONAL = {"personal_commentary", "expert_commentary", "executive_commentary", "expert_newsletter"}
BOARD = {"community", "developer_community", "research_community", "forum", "ai_benchmark_platform", "ai_benchmark_analysis"}
MEDIA = {"ai_media", "tech_media"}
def role_of(s):
    if s.get("user_source") or str(s.get("source_id", "")).startswith("user_rss_"): return "custom"
    scope, tier = s.get("source_scope") or "", s.get("provenance_tier") or ""
    if tier == "tier2_personal_social" or scope in PERSONAL: return "personal"
    if scope in BOARD: return "leaderboard"
    if scope in MEDIA or tier == "tier1_curated": return "media"
    return "official" if (scope or tier) else "media"

ALIASES = {"deepseek-ai": "deepseek", "alibaba_cloud": "alibaba", "qwenlm": "alibaba"}   # 与 sourceTaxonomy.js 的 OWNER_ALIASES 逐项一致
LOCAL = {"openai": "openai", "anthropic": "anthropic", "google": "google", "xai": "xai", "deepseek": "deepseek",
         "alibaba": "qwen", "zai": "zai", "bytedance_seed": "bytedance-seed", "cursor": "cursor", "opencode": "opencode",
         "nousresearch": "nousresearch", "huggingface": "huggingface", "qbitai": "qbitai", "ycombinator": "ycombinator"}
logo_cache = {}
def logo_for(s):
    owner = (s.get("source_owner") or "").strip().lower()
    owner = ALIASES.get(owner, owner)
    if s.get("avatar_url"):
        key = "av:" + s["avatar_url"]
        if key not in logo_cache:
            raw = cached(s["avatar_url"]); logo_cache[key] = to_data_uri(raw, 72) if raw else None
        return logo_cache[key]
    if owner == "dorami":
        key = "dorami"
        if key not in logo_cache:
            logo_cache[key] = to_data_uri(open(os.path.join(REPO, "frontend/public/brand/dorami-logo-128.png"), "rb").read(), 72)
        return logo_cache[key]
    if owner in LOCAL:
        key = "local:" + owner
        if key not in logo_cache:
            logo_cache[key] = to_data_uri(open(os.path.join(REPO, "frontend/public/source-logos", LOCAL[owner] + ".png"), "rb").read(), 72)
        return logo_cache[key]
    host = urllib.parse.urlparse(s.get("base_url") or "").netloc
    if not host: return None
    key = "fav:" + host
    if key not in logo_cache:
        raw = cached(f"https://www.google.com/s2/favicons?domain={urllib.parse.quote(host)}&sz=128")
        logo_cache[key] = to_data_uri(raw, 72) if raw else None
    return logo_cache[key]

src_raw = J("reader_sources.json")
SOURCES = {}
for s in src_raw["sources"]:
    if s.get("hidden"): continue
    SOURCES[s["source_id"]] = {
        "id": s["source_id"], "name": s["name"], "desc": s.get("description") or "", "role": role_of(s),
        "shape": s.get("shape") or "article", "kind": s.get("default_kind_label") or "", "platform": s.get("platform") or "",
        "count": s.get("count") or 0, "last": s.get("last_fetched") or "", "subs": s.get("subscriber_count") or 0,
        "on": bool(s.get("subscribed")), "host": urllib.parse.urlparse(s.get("base_url") or "").netloc.replace("www.", ""),
        "logo": logo_for(s), "mono": (s.get("name") or "?")[:1],
    }
SUBSCRIBED = src_raw.get("subscribed_source_ids") or []

def tags_of(a):
    out = []
    for t in (a.get("display_tags") or a.get("tags") or [])[:6]:
        lab = t.get("label") or t.get("name_zh") or t.get("name_en")
        if lab: out.append({"l": lab, "c": t.get("type") or "canonical"})
    return out

def slim(a):
    return {
        "id": a["id"], "title": a.get("title") or "", "zh": a.get("title_zh") or "", "src": a.get("source_id"),
        "url": a.get("source_url") or "", "at": a.get("publish_date") or "", "sum": a.get("summary_zh") or "",
        "nv": a.get("news_value"), "nvr": a.get("news_value_reason") or "", "cv": a.get("content_value"),
        "cvr": a.get("content_value_reason") or "", "dim": a.get("primary_dimension") or "news",
        "score": a.get("primary_score") if a.get("primary_score") is not None else a.get("quality_score"),
        "kind": a.get("kind") or "news", "kindl": a.get("kind_label") or "", "ev": a.get("event_label") or "",
        "genre": a.get("content_genre_label") or "", "tags": tags_of(a), "reads": a.get("read_count") or 0,
    }

# ── 文章列表 + 正文 ──
arts = [slim(a) for a in J("articles_60.json")["items"]]
_f30 = glob.glob(os.path.join(API, "articles_limit_30*shape_article*"))
facets = (json.load(open(_f30[0], encoding="utf-8")).get("kind_facets") if _f30 else None) or []
detail_files = glob.glob(os.path.join(API, "art_*.json")) + glob.glob(os.path.join(API, "articles_web_*.json")) + glob.glob(os.path.join(API, "articles_podcast_*.json"))
DETAILS = {}
img_budget = {"n": 0}
def localize_images(md, aid, cap):
    def rep(m):
        url = m.group(2)
        if not url.startswith("http"): return ""
        if img_budget["n"] >= 70 or rep.count >= cap: return ""
        raw = cached(url)
        if not raw: return ""
        try:
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            if im.width < 200: return ""
            if im.width > 1360: im = im.resize((1360, round(im.height * 1360 / im.width)), Image.LANCZOS)
            name = hashlib.sha1(url.encode()).hexdigest()[:14] + ".jpg"
            im.save(os.path.join(IMG, name), "JPEG", quality=80, optimize=True)
            rep.count += 1; img_budget["n"] += 1
            return f"![{m.group(1)}](assets/img/{name})"
        except Exception:
            return ""
    rep.count = 0
    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)[^)]*\)", rep, md)

brief_raw = J("brief_today.json")["edition"]
brief_ids = [it["article_id"] for it in brief_raw["items"]]
feature = set(brief_ids[:3]) | {a["id"] for a in arts[:4]}
for f in detail_files:
    d = json.load(open(f, encoding="utf-8"))
    aid = d["id"]
    md = d.get("content") or ""
    md = localize_images(md, aid, 5 if aid in feature else 2)
    s = slim(d)
    s["md"] = md[:22000]
    s["chars"] = len(d.get("content") or "")
    if d.get("podcast"):
        p = d["podcast"]
        s["pod"] = {"show": p.get("show_title"), "dur": p.get("duration_seconds"), "ep": p.get("episode"),
                    "ready": p.get("full_analysis_ready"), "guide": (p.get("premium_guide") or {}),
                    "premium": bool(d.get("is_premium_podcast"))}
    DETAILS[aid] = s

# ── 早报 ──
def brief_item(it):
    sn = it["snapshot"]; d = DETAILS.get(it["article_id"]) or {}
    return {
        "id": it["article_id"], "section": it["section"], "lane": it.get("selection_lane"), "score": it.get("quality_score"),
        "dim": sn.get("score_dimension") or "news", "why": sn.get("score_reason") or "", "reason": it.get("selection_reason") or "",
        "title": sn.get("title") or "", "zh": d.get("zh") or "", "src": sn.get("source_id"), "srcName": sn.get("source_name"),
        "at": sn.get("publish_date"), "sum": sn.get("summary") or "", "event": sn.get("event_id"),
        "related": [{"src": r.get("source_id"), "name": r.get("source_name"), "title": r.get("title_zh") or r.get("title"), "role": r.get("source_role")} for r in (sn.get("related_sources") or [])],
        "tags": [t.get("label") for t in (sn.get("display_tags") or []) if t.get("label")][:5],
        "cv": sn.get("content_value"), "cvr": sn.get("content_value_reason") or "",
    }
BRIEF = {"date": brief_raw["report_date"], "gen": brief_raw["generated_at"], "rev": brief_raw["revision"],
         "stats": brief_raw.get("selection_stats") or {}, "items": [brief_item(i) for i in brief_raw["items"]]}
hist = J("reader_briefs_limit_100.json") or {"items": []}
BRIEF["history"] = [{"date": h["report_date"], "status": h["status"]} for h in hist["items"]]

# ── 事件 / 榜单 ──
def ev_slim(e):
    return {"id": e["event_id"], "rank": e["rank"], "prev": e.get("previous_rank"), "title": e["title"], "sum": e.get("summary") or "",
            "pubs": e.get("publisher_count"), "n": e.get("member_count"), "first": e.get("first_report_at"), "latestAt": e.get("latest_at"),
            "latest": {"name": (e.get("latest") or {}).get("source_name"), "src": (e.get("latest") or {}).get("source_id"), "at": (e.get("latest") or {}).get("published_at")},
            "isNew": e.get("is_new"), "recent": e.get("is_recent"),
            "publishers": [{"src": p.get("source_id"), "name": p.get("name")} for p in (e.get("publishers") or [])]}
EVENTS = {"week": [ev_slim(e) for e in J("reader_events_window_hours_168_limit_10.json")["items"]],
          "d2": [ev_slim(e) for e in (J("events_48.json") or {"items": []})["items"]]}
EVD = {}
for f in glob.glob(os.path.join(API, "event_*.json")) + glob.glob(os.path.join(API, "reader_events_1278_*.json")):
    e = json.load(open(f, encoding="utf-8"))
    d = ev_slim(e)
    rep = e.get("representative") or {}
    d["rep"] = {"id": rep.get("article_id"), "title": rep.get("title"), "src": rep.get("source_id"), "name": rep.get("source_name"), "role": rep.get("source_role"), "at": rep.get("publish_date"), "url": rep.get("url")}
    d["members"] = [{"id": m.get("article_id"), "title": m.get("title"), "zh": m.get("title_zh") or "", "src": m.get("source_id"), "name": m.get("source_name"),
                     "role": m.get("source_role"), "at": m.get("report_at") or m.get("publish_date"), "sum": m.get("summary") or "", "rep": bool(m.get("is_representative"))} for m in (e.get("members") or [])]
    EVD[e["event_id"]] = d
def pick_slim(p):
    return {"id": p["id"], "title": p.get("title") or "", "zh": p.get("title_zh") or "", "src": p.get("source_id"), "name": p.get("source_name"),
            "at": p.get("publish_date"), "cv": p.get("content_value"), "kindl": p.get("kind_label") or "", "sum": p.get("summary") or "", "ready": p.get("full_analysis_ready")}
PICKS = {"article": [pick_slim(p) for p in (J("picks_article.json") or {"items": []})["items"]],
         "podcast": [pick_slim(p) for p in (J("reader_rankings_picks_shape_podcast.json") or {"items": []})["items"]]}

# ── 播客 ──
covers = {}
def pod_slim(a):
    p = a.get("podcast") or {}
    sid = a.get("source_id")
    if sid not in covers:
        raw = cached(p["image_url"]) if p.get("image_url") else None
        covers[sid] = to_data_uri(raw, 176) if raw else None
    s = slim(a)
    s.update({"dur": p.get("duration_seconds"), "show": p.get("show_title"), "premium": bool(a.get("is_premium_podcast")),
              "ready": bool(p.get("full_analysis_ready")), "guide": (p.get("premium_guide") or {}).get("status") or ""})
    return s
PODS = [pod_slim(a) for a in (J("podcasts_30.json") or {"items": []})["items"]]
texts = J("podcasts_episodes_podcast_ai_daily_brief_23581dd5be0a44d9_texts.json") or {"items": []}
PODTEXT = {t["kind"]: t["text"] for t in texts["items"]}

# ── 社交 ──
SOCIAL = []
sf = glob.glob(os.path.join(API, "articles_limit_30*shape_social*"))
if sf:
    for a in json.load(open(sf[0], encoding="utf-8"))["items"][:26]:
        try: e = json.loads(a.get("extensions_json") or "{}")
        except Exception: e = {}
        rp = e.get("reposted") or None
        def av(u):
            if not u: return None
            raw = cached(u); return to_data_uri(raw, 80) if raw else None
        media = []
        for u in (e.get("media_urls") or [])[:2]:
            raw = cached(u)
            if raw:
                try:
                    im = Image.open(io.BytesIO(raw)).convert("RGB")
                    if im.width > 1100: im = im.resize((1100, round(im.height * 1100 / im.width)), Image.LANCZOS)
                    name = "s" + hashlib.sha1(u.encode()).hexdigest()[:13] + ".jpg"; im.save(os.path.join(IMG, name), "JPEG", quality=80)
                    media.append("assets/img/" + name)
                except Exception: pass
        SOCIAL.append({"id": a["id"], "src": a.get("source_id"), "at": a.get("publish_date"), "name": e.get("author_name"), "handle": e.get("author_handle"),
                       "avatar": av(e.get("author_avatar_url_large") or e.get("author_avatar_url")), "text": a.get("content") or "", "zh": a.get("title_zh") or "",
                       "sum": a.get("summary_zh") or "", "lang": e.get("lang") or "", "media": media, "url": a.get("source_url") or "",
                       "repost": ({"name": rp.get("author_name"), "handle": rp.get("author_handle"), "avatar": av(rp.get("author_avatar_url_large") or rp.get("author_avatar_url")), "text": rp.get("text") or ""} if rp else None)})

COLL = [{"id": c["collection_id"], "name": c["name"], "desc": c.get("description") or "", "ids": c.get("source_ids") or []} for c in (J("reader_collections.json") or {"collections": []})["collections"]]

# ── 标题按词断行:预先算好「短语块」,前端在块之间放 <wbr>(见 proto.js 的 T())──
# 样页用 jieba 分词 + 本批数据里的标签名当自定义词典;落地时可在入库分析阶段产出同样的断点。
SEG = {}
try:
    import jieba, logging
    jieba.setLogLevel(logging.ERROR)
    words = {"英伟达", "字节跳动", "多模态", "黄仁勋", "智能体", "大模型", "开源模型", "上下文", "操作系统", "哆啦美"}
    for a in arts + list(DETAILS.values()):
        for t in a.get("tags") or []:
            if t.get("l") and re.fullmatch(r"[一-鿿]{2,6}", t["l"]): words.add(t["l"])
    for w in words: jieba.add_word(w)
    TAIL = set("，。、；：！？）」』》”’％%·…—")
    HEAD = set("（「『《“‘")
    PART = set("的了着过们吗呢吧啊")
    def is_cjk(ch): return "一" <= ch <= "鿿"
    RUN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+/%'’-]*|[BSu4e00-BSu9fff]+|BSs+|.".replace("BS", chr(92)))
    def units(title):
        for m in RUN.finditer(title):
            t = m.group(0)
            if is_cjk(t[0]): yield from jieba.cut(t, HMM=False)
            else: yield t
    def chunks(title):
        out, cur = [], ""
        for t in units(title):
            glue = t.isspace() or all(c in TAIL for c in t) or (len(t) == 1 and t in PART)
            if glue:                                   # 空格、句读、助词:跟着前面走
                if cur: cur += t
                elif out: out[-1] += t
                else: cur += t
            elif all(c in HEAD for c in t):            # 前引号、前括号:跟着后面走
                cur += t
            elif len(t) == 1 and is_cjk(t):            # 单字不落单
                if out and not cur and re.search(r"[0-9]" + chr(92) + "s?$", out[-1]): out[-1] += t   # 数字后的量词
                elif sum(1 for c in cur if is_cjk(c)) >= 2: out.append(cur); cur = t
                else: cur += t
            elif t[0].isdigit() and out and not cur and re.search(r"[A-Za-z] $", out[-1]):
                out[-1] += t                           # 「Fable 5.2」「Opus 5.5」:名字和版本号不拆开
            else:
                out.append(cur + t); cur = ""
        if cur:
            if out: out[-1] += cur
            else: out.append(cur)
        return out
    titles = set()
    for a in arts + list(DETAILS.values()) + PODS: titles.update([a.get("title"), a.get("zh")])
    for b in BRIEF["items"]:
        titles.update([b["title"], b["zh"]]); titles.update(r["title"] for r in b["related"])
    for e in EVENTS["week"] + EVENTS["d2"] + list(EVD.values()): titles.add(e["title"])
    for e in EVD.values():
        for m in e["members"]: titles.update([m["title"], m["zh"]])
    for p in PICKS["article"] + PICKS["podcast"]: titles.update([p["title"], p["zh"]])
    for t in titles:
        if t and re.search(r"[一-鿿]", t): SEG[t] = chunks(t)
except ImportError:
    print("jieba 未安装:标题退回浏览器默认断行")

D = {"snapshot": "2026-11-10 生产游客接口快照(只读)", "now": "2026-11-10T02:40:00Z", "sources": SOURCES, "subscribed": SUBSCRIBED,
     "brief": BRIEF, "articles": arts, "facets": facets, "details": DETAILS, "events": EVENTS, "eventDetails": EVD, "picks": PICKS,
     "pods": PODS, "covers": covers, "podText": PODTEXT, "social": SOCIAL, "collections": COLL, "seg": SEG}
js = "window.D = " + json.dumps(D, ensure_ascii=False, separators=(",", ":")) + ";\n"
open(os.path.join(OUT, "data.js"), "w", encoding="utf-8").write(js)
print("data.js", round(len(js.encode()) / 1024), "KB | sources", len(SOURCES), "logos", sum(1 for s in SOURCES.values() if s["logo"]),
      "| arts", len(arts), "details", len(DETAILS), "| brief", len(BRIEF["items"]), "| events", len(EVENTS["week"]), len(EVD),
      "| pods", len(PODS), "covers", sum(1 for c in covers.values() if c), "| social", len(SOCIAL), "| imgs", img_budget["n"], "| seg", len(SEG))
