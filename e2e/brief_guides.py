"""#269: real edition/API/player; generation transitions mocked without provider calls."""
from datetime import datetime, timezone
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from playwright.sync_api import expect

from e2e.podcast_fixture import SOURCE
from e2e.reader_fixture import USERNAME, PASSWORD, SOURCE_A


def seed_edition(database, username=USERNAME):
    assert (database.parent / '.dorami-e2e').is_file()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    from models.db import PersonalDigestEditionRecord, PersonalDigestItemRecord

    def insert(db, table, record):
        values = record.model_dump(exclude_none=True)
        keys = ','.join(values)
        marks = ','.join('?' for _ in values)
        return db.execute(f'INSERT INTO {table}({keys}) VALUES({marks})', tuple(values.values())).lastrowid
    now = datetime.now(timezone.utc)
    stamp, day = now.isoformat(), now.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    ids = [f'{SOURCE}-both-1', f'{SOURCE}-text-failed-1', f'{SOURCE}-both-0',
           f'{SOURCE}-queued-1', f'{SOURCE_A}-00']
    with sqlite3.connect(database) as db:
        db.row_factory = sqlite3.Row
        db.execute("UPDATE app_settings SET value='true' WHERE key='personal_digest_enabled'")
        edition = insert(db, 'personal_digest_editions', PersonalDigestEditionRecord(
            owner_username=username, report_date=day, revision=100, status='ready',
            check_after=stamp, cutoff_at=stamp, generated_at=stamp, created_at=stamp, updated_at=stamp))
        for i, aid in enumerate(ids):
            a = dict(db.execute('SELECT * FROM articles WHERE id=?', (aid,)).fetchone())
            snapshot = {k: a[k] for k in ('title', 'source_id', 'content_type', 'source_url', 'publish_date')}
            snapshot.update(source_name='播客导读测试' if i < 4 else '文章测试',
                            summary='早报导读交互样例；音频为本地合成测试文件，不调用付费服务。',
                            score_dimension='content', content_value=9.8-i*.4)
            insert(db, 'personal_digest_items', PersonalDigestItemRecord(
                edition_id=edition, article_id=aid, position=i,
                section='播客精选' if i == 2 else '今日精选',
                selection_lane='listen' if i == 2 else 'quality', quality_score_snapshot=9.8-i*.4,
                snapshot_json=json.dumps(snapshot, ensure_ascii=False), created_at=stamp))
    return ids


def run_brief_guide_flows(browser, base_url, database, artifacts, result):
    ids = seed_edition(database)
    checks = result.setdefault('brief_guide_checks', [])
    for width, theme in [(1280, 'light'), (1280, 'dark'), (360, 'light'), (390, 'dark'), (430, 'light')]:
        context = browser.new_context(viewport={'width': width, 'height': 900},
                                      is_mobile=width < 1025, has_touch=width < 1025, service_workers='block')
        context.set_default_timeout(15000)
        context.add_init_script(f"localStorage.setItem('dorami-theme','{theme}');localStorage.setItem('dorami-motion','reduce')")
        page = context.new_page()
        row = {'width': width, 'theme': theme, 'page_errors': [], 'generation_requests': 0,
               'generation_mocked': True, 'status': 'failed'}
        checks.append(row)
        page.on('pageerror', lambda error: row['page_errors'].append(str(error)))
        stage = {'value': None, 'reject': True, 'load_error': False, 'article_reads': 0}

        def route_request(route):
            req = route.request
            path = urlsplit(req.url).path
            if req.method == 'GET' and path == f'/api/articles/{ids[1]}':
                stage['article_reads'] += 1
            if urlsplit(req.url).netloc != urlsplit(base_url).netloc:
                route.abort()
            elif req.method == 'POST' and '/ai/' in path:
                row['generation_requests'] += 1
                if stage['reject']:
                    # Actual backend denial while generation is disabled; no provider exists in this sandbox.
                    route.continue_()
                else:
                    stage['value'] = 'queued'
                    route.fulfill(json={'outcome': 'queued', 'guide_status': 'queued', 'started': True})
            elif path == f'/api/articles/{ids[1]}' and (stage['value'] or stage['load_error']):
                if stage['load_error']:
                    route.fulfill(status=stage['load_error'], json={'detail': '导读状态暂时无法读取，请重试'})
                    return
                data = route.fetch().json()
                data['podcast']['premium_guide']['status'] = stage['value']
                if stage['value'] == 'ready':
                    data['podcast']['condensed_audio_url'] = f'/api/reader/podcast-artifacts/{ids[0]}-audio/audio'
                    data['podcast']['condensed_duration_seconds'] = 12
                route.fulfill(json=data)
            else:
                route.continue_()

        context.route('**/*', route_request)
        try:
            assert context.request.post(base_url+'/api/auth/login', data={'username': USERNAME, 'password': PASSWORD}).status == 200
            page.goto(base_url+'/#/reader')
            expect(page.locator('.bp-sheet')).to_be_visible()
            actions = page.locator('[data-guide-article]')
            expect(actions).to_have_count(4)
            headline = page.locator(f'[data-guide-article="{ids[0]}"]')
            target = page.locator(f'[data-guide-article="{ids[1]}"]')
            lane = page.locator(f'[data-guide-article="{ids[2]}"]')
            queued = page.locator(f'[data-guide-article="{ids[3]}"]')
            expect(headline).to_have_text('播放导读')
            expect(target).to_have_text('生成导读音频')
            expect(lane).to_have_text('播放导读')
            expect(queued).to_have_attribute('aria-disabled', 'true')
            headline.click()
            page.wait_for_function("[...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 0)")
            expect(headline).to_have_attribute('aria-pressed', 'true')
            headline.click()
            expect(headline).to_have_attribute('aria-pressed', 'false')
            if width == 1280:
                # Keyboard closing returns focus to the actual early-brief entry.
                close = page.get_by_role('button', name='关闭播放条（停止播放）', exact=True)
                close.focus()
                close.press('Enter')
                expect(headline).to_be_focused()
                row['keyboard_close_restores_entry'] = True
            assert page.locator('.bp-sheet').is_visible(), 'playing must not navigate to article'
            row['published_audio_played'] = True
            with page.expect_response(lambda response: '/ondemand' in response.url) as denied:
                target.click()
            assert denied.value.status == 403
            expect(page.get_by_text(denied.value.json()['detail'], exact=False).first).to_be_visible()
            expect(target).to_have_text('生成导读音频')
            stage['reject'] = False
            target.evaluate('(el) => { el.click(); el.click(); }')
            expect(target).to_have_text('导读音频生成中…')
            assert row['generation_requests'] == 2, 'double click must issue one POST'
            queued.evaluate('(el) => el.click()')
            assert row['generation_requests'] == 2
            stage['value'] = 'ready'
            expect(target).to_have_text('播放导读', timeout=12000)
            target.click()
            page.wait_for_function("[...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 0)")
            expect(target).to_have_attribute('aria-pressed', 'true')
            target.click()
            stage['load_error'] = 503
            page.evaluate("window.dispatchEvent(new Event('focus'))")
            expect(target).to_have_text('重试加载导读')
            assert row['generation_requests'] == 2
            stage['load_error'] = False
            target.click()
            expect(target).to_have_text('播放导读')
            assert row['generation_requests'] == 2
            if width == 1280 and theme == 'light':
                stage['load_error'] = 404
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                expect(target).to_have_text('重试加载导读')
                failed_reads = stage['article_reads']
                page.evaluate("window.dispatchEvent(new Event('focus'))")
                # Advance past the retry deadline; neither timer nor focus may retry 404.
                page.wait_for_timeout(12500)
                assert stage['article_reads'] == failed_reads
                stage['load_error'] = False
                target.click()
                expect(target).to_have_text('播放导读')
                assert stage['article_reads'] == failed_reads + 1
                with page.expect_response(lambda response: urlsplit(response.url).path == f'/api/articles/{ids[1]}'):
                    page.evaluate("window.dispatchEvent(new Event('focus'))")
                assert stage['article_reads'] == failed_reads + 2
                assert row['generation_requests'] == 2
                row['missing_article_manual_retry_only'] = True
            page.keyboard.press("Tab")
            target.focus()
            assert target.evaluate("el => getComputedStyle(el).outlineStyle") != 'none'
            for button in actions.all():
                button.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
                box = button.bounding_box()
                assert box and box['x'] >= 0 and box['x'] + box['width'] <= width + 1
                if width < 1025:
                    assert box['height'] >= 44
                hit = button.evaluate('(el) => { const r=el.getBoundingClientRect(); const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2); return {ok:el.contains(hit),label:el.textContent,hit:hit?.outerHTML.slice(0,200),rect:{x:r.x,y:r.y}}; }')
                assert hit['ok'], hit
            if width == 1280 and theme == 'light':
                headline.evaluate("el => el.scrollIntoView({block: 'center', behavior: 'instant'})")
                broken = page.add_style_tag(content='.bp-guide { pointer-events: none !important; }')
                assert not headline.evaluate('(el) => { const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }')
                broken.evaluate('(el) => el.remove()')
                row['negative_control_detected'] = True
            target.scroll_into_view_if_needed()
            page.screenshot(path=str(artifacts/f'brief-guides-{width}-{theme}.png'))
            assert not row['page_errors'], row['page_errors']
            row['status'] = 'passed'
        except Exception:
            page.screenshot(path=str(artifacts/f'brief-guides-{width}-{theme}-failure.png'))
            raise
        finally:
            context.close()
    # Guest sees both actions, can play, and is prompted to log in before any generation POST.
    context = browser.new_context(viewport={'width': 430, 'height': 900}, is_mobile=True, has_touch=True,
                                  service_workers='block')
    guest_posts = []
    def guard_guest(route):
        request = route.request
        if request.method == 'POST' and '/ai/' in request.url:
            guest_posts.append(request.url)
            route.abort()
        elif urlsplit(request.url).netloc != urlsplit(base_url).netloc:
            route.abort()
        else:
            route.continue_()
    context.route('**/*', guard_guest)
    try:
        assert context.request.post(base_url+'/api/auth/guest', data={'entry': 'e2e'}).status == 200
        seed_edition(database, username='guest:shared')
        page = context.new_page()
        page.goto(base_url+'/#/reader')
        target = page.locator(f'[data-guide-article="{ids[1]}"]')
        expect(target).to_have_text('生成导读音频')
        target.click()
        expect(page.get_by_text('登录后即可生成导读音频', exact=True)).to_be_visible()
        assert not guest_posts
        page.get_by_role('button', name='继续逛逛', exact=True).click()
        page.locator(f'[data-guide-article="{ids[0]}"]').click()
        page.wait_for_function("[...document.querySelectorAll('audio')].some(a => !a.paused && a.currentTime > 0)")
        checks.append({'role': 'guest', 'width': 430, 'generation_requests': 0,
                       'published_audio_played': True, 'status': 'passed'})
    finally:
        context.close()
    with sqlite3.connect(database) as db:
        assert db.execute('SELECT count(*) FROM bailian_tts_calls').fetchone()[0] == 0
        db.execute("UPDATE app_settings SET value='false' WHERE key='personal_digest_enabled'")
