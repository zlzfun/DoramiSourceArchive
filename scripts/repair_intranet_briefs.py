"""内网日报脏数据修复:从生产公开聚合接口取干净正文,PUT 覆写本地脏日报。

背景:2026-08 生产日报空正文事故(思考型模型耗尽输出配额)修复后,生产 60 期
已全部重汇编干净;但内网同步进来的副本不会自愈——增量游标按 fetched_date 走
(修复未动它),且导入合并对已存在 has_content=True 的记录不覆盖。
日报又被知识台账刻意排除(DataTab exclude_source_ids)、也不是节点,
界面删不着,故走本脚本:不删记录、不动同步游标、幂等可重跑。

用法(在内网服务器项目根,后端需在运行):
  DORAMI_FEED_TOKEN=dfeed_xxx \
  .venv/bin/python scripts/repair_intranet_briefs.py [--local http://127.0.0.1:8088] [--dry-run]

  运行时交互输入内网 admin 账号密码(不落盘不回显);
  生产地址默认 https://www.dorami.cloud,可用 DORAMI_PROD_BASE 覆盖。
"""
import argparse
import getpass
import os
import sqlite3
import sys

import httpx

FOOTER = "由哆啦美·归档中枢生成"
BRIEF_SOURCE = "dorami_daily_brief"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", default="http://127.0.0.1:8088", help="内网后端地址")
    ap.add_argument("--db", default="data/cms_data.db", help="内网 SQLite 路径(只读,用于找脏期)")
    ap.add_argument("--dry-run", action="store_true", help="只报告,不写回")
    args = ap.parse_args()

    feed_token = (os.environ.get("DORAMI_FEED_TOKEN") or "").strip()
    if not feed_token.startswith("dfeed_"):
        print("需要环境变量 DORAMI_FEED_TOKEN(dfeed_ 开头的生产聚合令牌)", file=sys.stderr)
        return 2
    prod = (os.environ.get("DORAMI_PROD_BASE") or "https://www.dorami.cloud").rstrip("/")

    # 1. 本地找脏期(NULL / 缺落款)
    con = sqlite3.connect(args.db)
    dirty = con.execute(
        """select id, length(content) from articles
           where source_id=? and (content is null or content='' or content not like ?)
           order by publish_date""",
        (BRIEF_SOURCE, f"%{FOOTER}%"),
    ).fetchall()
    con.close()
    if not dirty:
        print("本地日报全部完整,无需修复。")
        return 0
    print(f"本地脏日报 {len(dirty)} 期:{[r[0].removeprefix('daily_brief_') for r in dirty]}")

    # 2. 拉生产干净正文(公开聚合接口,与神灯流水线同源)
    r = httpx.get(
        f"{prod}/api/public/feed/articles",
        params={"source_ids": BRIEF_SOURCE, "include_content": "true", "limit": 500},
        headers={"Authorization": f"Bearer {feed_token}"},
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    arts = data if isinstance(data, list) else (data.get("articles") or data.get("items") or [])
    prod_content = {a["id"]: (a.get("content") or "") for a in arts}
    print(f"生产日报取到 {len(prod_content)} 期")

    if args.dry_run:
        for rid, old_len in dirty:
            src = prod_content.get(rid, "")
            state = "可修" if FOOTER in src else ("生产也无此期/不完整" if src is not None else "生产缺失")
            print(f"[dry-run] {rid} 本地 len={old_len} → {state}")
        return 0

    # 3. 登录内网 admin(密码交互输入,不回显不落盘)
    user = input("内网 admin 用户名: ").strip()
    pw = getpass.getpass("密码: ")
    with httpx.Client(base_url=args.local, timeout=60) as c:
        lr = c.post("/api/auth/login", json={"username": user, "password": pw})
        lr.raise_for_status()
        # cookie_secure=true 时 Secure cookie 不回发 http,显式拼头兼容两种姿态
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in lr.cookies.items())}

        ok = failed = 0
        for rid, old_len in dirty:
            content = prod_content.get(rid, "")
            if FOOTER not in content:
                print(f"[{rid}] SKIP 生产侧无完整正文(len={len(content)})")
                failed += 1
                continue
            resp = c.put(f"/api/articles/{rid}", headers=headers, json={"content": content})
            if resp.status_code == 200:
                print(f"[{rid}] OK {old_len} -> {len(content)}")
                ok += 1
            else:
                print(f"[{rid}] FAIL HTTP {resp.status_code}: {resp.text[:120]}")
                failed += 1
        print(f"完成:修复 {ok} 期,失败/跳过 {failed} 期")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
