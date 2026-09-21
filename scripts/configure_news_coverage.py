#!/usr/bin/env python3
"""Issue #127 rollout. Read-only by default; stop backend/workers before --apply.

Creates one additional daytime collection job and extends an explicit public
brief allowlist. Existing jobs, source analysis switches and user interests are
untouched. Snapshot rollback refuses to overwrite subsequent operator edits.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

MARKER = 'news-coverage-issue-127'
SCOPE = 'daily_brief_source_ids'
REVISION = 'daily_brief_selection_revision'
JOB = {
    'name': 'AI 新闻日间补采',
    'description': MARKER,
    'fetcher_ids_json': json.dumps(['web_ithome_ai', 'rss_hn_ai']),
    'params_json': '{}',
    'per_fetcher_params_json': json.dumps({'web_ithome_ai': {'limit': 60}, 'rss_hn_ai': {'limit': 50}}),
    'cron_expr': '15 9-23 * * *',
    'is_active': 1,
    'downstream_policy_json': '{}',
    'legacy_task_id': None,
}


def setting(conn, key):
    row = conn.execute('SELECT value FROM app_settings WHERE key=?', (key,)).fetchone()
    return row[0] if row is not None else None


def write_setting(conn, key, value):
    if value is None:
        conn.execute('DELETE FROM app_settings WHERE key=?', (key,))
    else:
        conn.execute('INSERT INTO app_settings(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, value))


def plan(conn):
    if setting(conn, 'remote_sync:v2_consumer_mode') is not None:
        raise ValueError('只能在外部采集节点执行，当前数据库有同步接收标记')
    jobs = [dict(row) for row in conn.execute('SELECT * FROM collection_jobs WHERE description=?', (MARKER,))]
    if len(jobs) > 1 or (jobs and any(jobs[0][k] != v for k, v in JOB.items())):
        raise ValueError('专用任务已被修改或重复，拒绝覆盖，请在管理面核对')
    for source in ('web_ithome_ai', 'rss_hn_ai'):
        row = conn.execute('SELECT is_active, collection_authority_id FROM source_configs WHERE source_id=?', (source,)).fetchone()
        # Both IDs are built-in fetchers. Missing overrides mean enabled defaults.
        if row is not None and (not row['is_active'] or row['collection_authority_id']):
            raise ValueError(f'{source} 被停用或由远端管理，请先核对节点')
    before = {key: setting(conn, key) for key in (SCOPE, REVISION)}
    raw = before[SCOPE]
    scope = json.loads(raw) if raw else None
    if scope is not None and (not isinstance(scope, list) or not all(isinstance(s, str) for s in scope)):
        raise ValueError('公共日报来源名单格式异常，拒绝覆盖')
    after = dict(before)
    if scope and 'rss_hn_ai' not in scope:
        after[SCOPE] = json.dumps([*scope, 'rss_hn_ai'], ensure_ascii=False)
        after[REVISION] = str(uuid4())
    return {'job_before': jobs[0] if jobs else None, 'job_after': jobs[0] if jobs else JOB,
            'settings_before': before, 'settings_after': after}


def save_snapshot(path, snapshot):
    # Exclusive creation prevents destroying the original rollback receipt.
    with path.open('x', encoding='utf-8') as stream:
        os.chmod(path, 0o600)
        json.dump(snapshot, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def apply_plan(conn, changes, snapshot_path, database):
    if changes['job_before'] is not None and changes['settings_before'] == changes['settings_after']:
        return False
    if changes['job_before'] is None:
        now = datetime.now(timezone.utc).isoformat()
        row = {**JOB, 'created_at': now, 'updated_at': now}
        fields = ','.join(row)
        placeholders = ','.join('?' for _ in row)
        result = conn.execute(f'INSERT INTO collection_jobs({fields}) VALUES ({placeholders})', list(row.values()))
        changes['job_after'] = dict(conn.execute('SELECT * FROM collection_jobs WHERE id=?', (result.lastrowid,)).fetchone())
    for key, value in changes['settings_after'].items():
        write_setting(conn, key, value)
    save_snapshot(snapshot_path, {'version': 1, 'database': str(database), **changes})
    return True


def rollback(conn, snapshot):
    job = snapshot['job_after']
    row = conn.execute('SELECT * FROM collection_jobs WHERE id=?', (job['id'],)).fetchone()
    current = dict(row) if row is not None else None
    expected = snapshot['job_before']
    current_settings = {key: setting(conn, key) for key in (SCOPE, REVISION)}
    if current == expected and current_settings == snapshot['settings_before']:
        return False
    if current != job or current_settings != snapshot['settings_after']:
        raise ValueError('配置在应用后发生变化，拒绝覆盖；请核对快照与管理面')
    if snapshot['job_before'] is None:
        conn.execute('DELETE FROM collection_jobs WHERE id=?', (job['id'],))
    for key, value in snapshot['settings_before'].items():
        write_setting(conn, key, value)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True, help='已有 SQLite 文件')
    parser.add_argument('--apply', action='store_true', help='写入；省略时仅预览')
    parser.add_argument('--offline', action='store_true', help='确认后端及 worker 均已停止；完成后必须重启加载调度')
    parser.add_argument('--snapshot', type=Path, help='应用时创建的新快照文件')
    parser.add_argument('--rollback', type=Path, help='预览/恢复此前快照')
    args = parser.parse_args(argv)
    if args.apply and not args.offline:
        parser.error('--apply 需要 --offline，先停止后端及 worker，完成后重启')
    if args.apply and not args.rollback and not args.snapshot:
        parser.error('--apply 需要 --snapshot 保存回滚凭据')
    database = args.database.resolve(strict=True)
    conn = sqlite3.connect(database.as_uri() + ('?mode=rw' if args.apply else '?mode=ro'), uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        if args.apply:
            conn.execute('BEGIN IMMEDIATE')
        if args.rollback:
            snapshot = json.loads(args.rollback.read_text())
            if snapshot.get('version') != 1 or snapshot.get('database') != str(database):
                raise ValueError('快照版本或数据库路径不匹配')
            if args.apply:
                changed = rollback(conn, snapshot)
            else:
                changed = None
            output = {'action': 'rollback', 'snapshot': snapshot, 'changed': changed}
        else:
            changes = plan(conn)
            changed = apply_plan(conn, changes, args.snapshot, database) if args.apply else None
            output = {'action': 'apply' if args.apply else 'preview', **changes, 'changed': changed}
        if args.apply:
            conn.commit()
        print(json.dumps(output, ensure_ascii=False, indent=2))
    finally:
        conn.close()


if __name__ == '__main__':
    main()
