"""Bounded public-source windows: fake HTTP, temporary archive, no provider."""
import asyncio
import datetime as dt
import json
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest

from fetchers.impl.publication_window import PublicationWindow
from fetchers.impl.rss_fetcher import HackerNewsAiRssFetcher as HN
from fetchers.impl.webpage_fetcher import IThomeAiWebFetcher as IT
from fetchers.impl.curated_core_fetcher import QbitAiWebsiteFetcher as QB

UTC = dt.timezone.utc
RSS = '<rss version="2.0"><channel><title>HN</title></channel></rss>'


@pytest.fixture
def bounds():
    before = (dt.datetime.now(UTC) - dt.timedelta(days=1)).replace(hour=12, minute=30, second=0, microsecond=0)
    return {'publish_after': (before-dt.timedelta(days=7)).isoformat(),
            'publish_before': before.isoformat(), 'limit': 1000, 'window_max_pages': 20}


def response(url, text=None, data=None):
    return httpx.Response(200, text=text, json=data, request=httpx.Request('GET', url))


def run(fetcher, params, public=False):
    async def collect():
        return [x async for x in (fetcher.fetch(**params) if public else fetcher._run(None, **params))]
    return asyncio.run(collect())


async def no_browser():
    return None


def hn_fixture(f, bounds, hits, *, page_size=100, rss=RSS, metadata=None):
    calls = []
    async def get(client, url, **kwargs):
        calls.append(url)
        if url.startswith(f.base_feed_url):
            return response(url, rss) if rss is not None else None
        page = int(parse_qs(urlparse(url).query)['page'][0])
        data = {'hits': hits[page*page_size:(page+1)*page_size], 'nbPages': (len(hits)+page_size-1)//page_size,
                'nbHits': len(hits), 'page': page, 'exhaustiveNbHits': True}
        if metadata:
            data.update(metadata(page))
        return response(url, data=data)
    f._safe_get = get
    f._open_web_backend = no_browser
    return calls


def hit(number, stamp, title='AI research'):
    return {'objectID':str(number), 'created_at_i':int(stamp.timestamp()), 'points':30, 'num_comments':2,
            'title':title, 'url':f'https://example.test/article/{number}', 'story_text':'original comment'}


@pytest.mark.parametrize('update', [
    {'publish_after':None}, {'publish_before':''}, {'publish_after':'2020-01-01'},
    {'publish_after':'bad'}, {'publish_after':'2020-01-01T00:00:00+08:00'},
    {'window_max_pages':0}, {'window_max_pages':21}, {'window_max_pages':True},
    {'window_max_pages':2.5}, {'limit':0}, {'limit':10001}, {'limit':False},
])
def test_window_rejects_invalid_before_network(bounds, update):
    with pytest.raises(ValueError):
        run(HN(), {**bounds, **update})


def test_window_pair_range_and_actual_clock(bounds):
    before=dt.datetime.fromisoformat(bounds['publish_before'])
    for update in [{'publish_after':before.isoformat()},
                   {'publish_after':(before-dt.timedelta(days=8)).isoformat()},
                   {'publish_before':(dt.datetime.now(UTC)+dt.timedelta(days=1)).isoformat()}]:
        with pytest.raises(ValueError): PublicationWindow.from_params({**bounds,**update},20)
    assert PublicationWindow.from_params({'window_max_pages':999},20) is None


def test_hn_public_fetch_ninth_page_schema_forwarding_and_inventory(bounds):
    f=HN(); before=dt.datetime.fromisoformat(bounds['publish_before'])
    hits=[hit(i+1,before-dt.timedelta(days=6,seconds=i)) for i in range(900)]
    calls=hn_fixture(f,bounds,hits)
    items=run(f,bounds,public=True)
    assert len(items)==900 and len(calls)==10  # RSS + 9 API pages
    assert all(x.content=='' and x.source_id=='rss_hn_ai' for x in items)
    assert f.coverage_report.summary()['complete']
    assert f.coverage_report.summary()['api_unique_items_examined']==900
    assert f.coverage_report.summary()['max_top_ordinal']==900
    query=parse_qs(urlparse(calls[1]).query)['numericFilters'][0]
    assert 'created_at_i>=' in query and 'created_at_i<=' in query
    # Six-day-old evidence is outside the normal 72h policy, which stays intact.
    f._active_min_points=10;f._active_min_comments=0
    assert f._algolia_entry(hits[0],int((dt.datetime.now(UTC)-dt.timedelta(hours=72)).timestamp())) is None


def test_hn_exact_point_bounds_after_conservative_api_rounding(bounds):
    after=dt.datetime.fromisoformat(bounds['publish_after'])+dt.timedelta(microseconds=500000)
    before=dt.datetime.fromisoformat(bounds['publish_before'])
    p={**bounds,'publish_after':after.isoformat()}
    f=HN();hn_fixture(f,p,[hit(1,after.replace(microsecond=0)),hit(2,after+dt.timedelta(seconds=1)),hit(3,before),hit(4,before+dt.timedelta(seconds=1))])
    items=run(f,p)
    assert {x.guid.rsplit('=',1)[-1] for x in items}=={'2','3'}
    assert all(x.raw_data['publication_date_proof']['date_precision']=='second' for x in items)


@pytest.mark.parametrize('metadata,expected', [
    (lambda p:{'nbPages':10,'nbHits':1000},'page_limit_or_incomplete'),
    (lambda p:{'nbPages':1,'nbHits':1001},'api_result_cap'),
    (lambda p:{'exhaustiveNbHits':False},'api_result_cap'),
])
def test_hn_cap_incomplete_is_external_error_with_safe_items(bounds,metadata,expected):
    f=HN();before=dt.datetime.fromisoformat(bounds['publish_before']);hn_fixture(f,bounds,[hit(1,before)],metadata=metadata)
    emitted=[]
    async def collect():
        async for x in f._run(None,**{**bounds,'window_max_pages':1}):emitted.append(x)
    with pytest.raises(RuntimeError,match='publication_window_incomplete'):asyncio.run(collect())
    assert len(emitted)==1 and expected in f.coverage_report.reasons
    assert emitted[0].raw_data['publication_window']['complete'] is False


def test_hn_undated_and_outside_rss_never_get_now(bounds):
    before=dt.datetime.fromisoformat(bounds['publish_before'])
    rss='<rss version="2.0"><channel><item><guid>missing</guid><title>AI missing</title></item><item><guid>future</guid><title>AI future</title><pubDate>Wed, 01 Jan 2042 00:00:00 GMT</pubDate></item></channel></rss>'
    f=HN();hn_fixture(f,bounds,[hit(1,before)],rss=rss);emitted=[]
    async def collect():
        async for x in f._run(None,**bounds):emitted.append(x)
    with pytest.raises(RuntimeError,match='rss_date_unknown'):asyncio.run(collect())
    assert len(emitted)==1 and emitted[0].title=='AI research'
    assert f.coverage_report.summary()['quarantine_count']==1


def test_hn_existing_ids_are_inventory_only_even_empty_bodies(bounds):
    f=HN();before=dt.datetime.fromisoformat(bounds['publish_before']);hn_fixture(f,bounds,[hit(1,before)])
    async def existing(ids): return {i:False for i in ids}
    f.dedup_lookup=existing
    assert run(f,bounds)==[]
    row=f.coverage_report.items[0]
    assert row['archive_status']=='reused_archive' and row['body_status']=='missing'
    assert f.coverage_report.summary()['complete']


def it_page(stamps,start=0):
    return '<ul class="bl">'+''.join(f'<li><div class="c" data-ot="{t.isoformat()}"><a class="title" href="https://www.ithome.com/1/004/{i+start:03d}.htm">AI {i+start}</a><div class="m">summary</div></div></li>' for i,t in enumerate(stamps))+'</ul>'


def it_fixture(f,pages,fail=False):
    calls=[]
    async def get(client,url):return response(f.listing_url,pages[0])
    async def post(client,url,**kwargs):
        calls.append((url,kwargs));index=len(calls)
        if fail:return None
        content=pages[index] if index<len(pages) else ''
        return response(url,data={'success':True,'content':{'count':1 if content else 0,'html':content}})
    f._safe_get=get;f._safe_post=post;f._open_web_backend=no_browser
    return calls


def test_ithome_fixed_window_crosses_six_pages_and_preserves_existing(bounds):
    f=IT();before=dt.datetime.fromisoformat(bounds['publish_before']);after=dt.datetime.fromisoformat(bounds['publish_after'])
    pages=[it_page([before-dt.timedelta(days=i)],start=i) for i in range(5)]+[it_page([after-dt.timedelta(seconds=1)],start=5)]
    calls=it_fixture(f,pages)
    async def existing(ids):return {i:True for i in ids if i==f._content_id('https://www.ithome.com/1/004/000.htm')}
    f.dedup_lookup=existing
    items=run(f,{**bounds,'fetch_detail':False})
    assert len(items)==4 and len(calls)==5
    assert f.coverage_report.summary()['discovered_count']==6
    assert f.coverage_report.summary()['reused_archive_count']==1
    cursors=[int(parse_qs(urlparse(x[0]).query)['ot'][0]) for x in calls]
    assert all(a>b for a,b in zip(cursors,cursors[1:]))
    assert all(x[1]=={'data':{}} for x in calls)
    assert all(x.raw_data['publication_date_proof']['date_precision']=='millisecond' for x in items)


@pytest.mark.parametrize('kind',['cap','network','repeat','limit'])
def test_ithome_partial_visible_not_success(bounds,kind):
    f=IT();before=dt.datetime.fromisoformat(bounds['publish_before']);first=it_page([before,before-dt.timedelta(days=1)])
    it_fixture(f,[first,first] if kind=='repeat' else [first],fail=kind=='network')
    params={**bounds,'fetch_detail':False}
    if kind=='cap':params['window_max_pages']=1
    if kind=='limit':params['limit']=1
    with pytest.raises(RuntimeError,match='publication_window_incomplete'):run(f,params)
    assert not f.coverage_report.summary()['complete']


def qbit_page(rows):
    return '<div class="article_list">'+''.join(f'<div class="picture_text"><div class="text_box"><h4><a href="https://www.qbitai.com/2026/11/{i}.html">AI {i}</a></h4><p>summary</p><div class="info"><span class="time">{raw}</span></div></div></div>' for i,raw in rows)+'</div>'


def qbit_detail(stamp,body='Original full body. '*30):
    local=stamp.astimezone(ZoneInfo('Asia/Shanghai'))
    return f'<div class="content"><div class="article_info"><span class="date">{local:%Y-%m-%d}</span><span class="time">{local:%H:%M:%S}</span></div><div class="article"><p>{body}</p></div></div>'


def qbit_fixture(f,pages,details):
    calls=[]
    async def get(client,url):
        calls.append(url)
        if url==f.listing_url:return response(url,pages[0])
        if '?cat=21&page=' in url:
            n=int(parse_qs(urlparse(url).query)['page'][0]);return response(url,pages[n-1]) if n<=len(pages) else None
        return response(url,details[url.rsplit('/',1)[-1][:-5]]) if url.rsplit('/',1)[-1][:-5] in details else None
    f._safe_get=get;f._open_web_backend=no_browser
    return calls


def test_qbit_boundary_precise_and_old_day_archive_untouched(bounds):
    f=QB();after=dt.datetime.fromisoformat(bounds['publish_after']);before=dt.datetime.fromisoformat(bounds['publish_before']);local=ZoneInfo('Asia/Shanghai')
    day=lambda t:t.astimezone(local).date().isoformat()
    pages=[qbit_page([(1,day(before-dt.timedelta(days=1)))]),qbit_page([(2,day(after)),(3,day(after))]),qbit_page([(4,day(after-dt.timedelta(days=1)))])]
    calls=qbit_fixture(f,pages,{'1':qbit_detail(before-dt.timedelta(days=1)),'2':qbit_detail(after+dt.timedelta(seconds=1)),'3':qbit_detail(after-dt.timedelta(seconds=1))})
    async def existing(ids):return {i:True for i in ids if i==f._content_id('https://www.qbitai.com/2026/11/2.html')}
    f.dedup_lookup=existing
    items=run(f,bounds,public=True)
    assert [x.title for x in items]==['AI 1']
    assert 'https://www.qbitai.com?cat=21&page=3' in calls
    assert not any('?cat=21&page=4' in u for u in calls)
    rows=f.coverage_report.items
    assert [x['status'] for x in rows]==['in_window','in_window','outside_window','outside_window']
    assert rows[1]['archive_status']=='reused_archive'
    assert rows[1]['date_proof']['published_at']==(after+dt.timedelta(seconds=1)).isoformat()
    assert not getattr(items[0],'_refresh_existing_metadata',False)


def test_qbit_middle_day_proof_is_interval_not_exact_midnight(bounds):
    f=QB();after=dt.datetime.fromisoformat(bounds['publish_after']);middle=after+dt.timedelta(days=3);zone=ZoneInfo('Asia/Shanghai')
    day=middle.astimezone(zone).date().isoformat();older=(after-dt.timedelta(days=1)).astimezone(zone).date().isoformat()
    qbit_fixture(f,[qbit_page([(1,day)]),qbit_page([(2,older)])],{'1':'<div class="article"><p>real body</p></div>'})
    emitted=[]
    async def collect():
        async for item in f._run(None,**bounds):emitted.append(item)
    with pytest.raises(RuntimeError,match='date_precision_requires_archive_support'):
        asyncio.run(collect())
    assert emitted==[]
    row=f.coverage_report.items[0]
    proof=row['date_proof']
    assert row['status']=='in_window' and row['quarantine_reason']=='date_precision_requires_archive_support'
    assert f.coverage_report.summary()['date_unknown_count']==0
    assert f.coverage_report.summary()['quarantine_count']==1
    assert proof['date_precision']=='day' and 'published_at' not in proof
    assert proof['date_timezone']=='Asia/Shanghai'
    assert dt.datetime.fromisoformat(proof['date_interval_end_exclusive'])-dt.datetime.fromisoformat(proof['date_interval_start'])==dt.timedelta(days=1)


def test_qbit_unknown_boundary_quarantined_no_now(bounds):
    f=QB();after=dt.datetime.fromisoformat(bounds['publish_after']);zone=ZoneInfo('Asia/Shanghai')
    qbit_fixture(f,[qbit_page([(1,after.astimezone(zone).date().isoformat())]),qbit_page([(2,(after-dt.timedelta(days=1)).astimezone(zone).date().isoformat())])],{})
    with pytest.raises(RuntimeError,match='date_unknown'):run(f,bounds)
    assert f.coverage_report.items[0]['status']=='date_unknown'


@pytest.mark.parametrize('kind',['cap','repeat','missing'])
def test_qbit_bounded_partial_pages(bounds,kind):
    f=QB();before=dt.datetime.fromisoformat(bounds['publish_before']);stamp=before-dt.timedelta(days=2);day=stamp.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
    first=qbit_page([(1,day)]);qbit_fixture(f,[first,first] if kind=='repeat' else [first],{'1':qbit_detail(stamp)})
    with pytest.raises(RuntimeError,match='publication_window_incomplete'):run(f,{**bounds,'window_max_pages':1 if kind=='cap' else 20})
    assert not f.coverage_report.summary()['complete']


@pytest.mark.parametrize('cls',[HN,IT,QB])
def test_public_fetch_schema_defaults_and_saved_job_parameters(bounds,cls):
    from api.collection_planning import build_collection_job_items
    from models.db import CollectionJobRecord
    f=cls();seen=[]
    async def fake_run(client,**kwargs):
        seen.append(kwargs)
        if False:yield
    f._run=fake_run;f._open_web_backend=no_browser
    job=CollectionJobRecord(id=1,name='window',fetcher_ids_json=json.dumps([f.source_id]),params_json='{"limit":20}',per_fetcher_params_json=json.dumps({f.source_id:bounds}))
    params=build_collection_job_items(job)[0]['params']
    run(f,params,public=True)
    assert seen==[bounds]
    assert cls.default_limit in (18,20)


def test_hn_same_instance_scoped_then_default_has_no_window_leak(bounds):
    f=HN();before=dt.datetime.fromisoformat(bounds['publish_before']);hn_fixture(f,bounds,[hit(1,before)])
    run(f,bounds)
    calls=[]
    async def feed(client,url,*args):
        calls.append(url)
        import feedparser
        return feedparser.parse(RSS)
    f._fetch_parsed_feed=feed
    assert run(f,{'limit':20})==[]
    assert f.coverage_report is None and f.api_window_hours==72 and f.api_max_pages==8


def test_raw_data_article_archive_roundtrip_and_reader_compatibility(bounds):
    from models.content import serialize_to_metadata, RssArticleContent, WebPageArticleContent
    from models.db import ArticleRecord
    from api.routers.archive_sync import archive_article_payload,build_import_article_record
    from api.articles_view import serialize_article_list_item
    # Current unchanged export/import contract carries arbitrary article extensions,
    # not candidate evidence. No new top-level sync fields or typed stream keys.
    proof={'date_precision':'day','date_timezone':'Asia/Shanghai','raw_date':'2026-01-01',
           'date_interval_start':'2025-12-31T16:00:00+00:00','date_interval_end_exclusive':'2026-01-01T16:00:00+00:00'}
    for cls in (RssArticleContent,WebPageArticleContent):
        item=cls(id='fixture',source_id='fixture',source_url='https://example.test/fixture',title='AI original',publish_date=bounds['publish_before'],content='full body',has_content=True,raw_data={'publication_date_proof':proof,'publication_window':{'complete':True}})
        ext=serialize_to_metadata(item)['extensions'];record=ArticleRecord(id=item.id,source_id=item.source_id,title=item.title,content_type=item.content_type,publish_date=item.publish_date,fetched_date=item.fetched_date,content=item.content,extensions_json=json.dumps(ext))
        restored=build_import_article_record(archive_article_payload(record))
        assert json.loads(restored.extensions_json)==ext
        assert restored.content==item.content and restored.publish_date==item.publish_date
        # Legacy raw-data consumers read known keys and ignore these additive keys.
        assert json.loads(restored.extensions_json)['raw_data'].get('published','')==''
        view=serialize_article_list_item(restored,include_extensions=True,source_kinds={})
        assert view['content']=='full body' and view['title']=='AI original'


def test_source_partial_isolated_by_normal_collection_runner(monkeypatch,bounds):
    import api.app as app
    f=HN();before=dt.datetime.fromisoformat(bounds['publish_before']);hn_fixture(f,bounds,[hit(1,before)],metadata=lambda p:{'exhaustiveNbHits':False})
    finished=[]
    monkeypatch.setattr(app,'create_collection_job_run',lambda **kw:1)
    monkeypatch.setattr(app,'finish_collection_job_run',lambda *args,**kw:finished.append(kw))
    async def tracked(sid,params,**kwargs):
        if sid=='rss_hn_ai':
            async for _ in f._run(None,**bounds):pass
        return {'fetcher_id':sid,'status':'success','saved_count':1}
    monkeypatch.setattr(app,'run_fetcher_with_tracking',tracked)
    result=asyncio.run(app.run_collection_items([{'fetcher_id':'rss_hn_ai'},{'fetcher_id':'other'}],name='fixture'))
    assert result['status']=='partial_failed' and result['failed_count']==1
    assert result['results'][1]['status']=='success' and result['saved_count']==1
    assert 'publication_window_incomplete' in result['error_message']
    assert finished[0]['status']=='partial_failed'


@pytest.mark.parametrize('cls',[HN,IT,QB])
@pytest.mark.parametrize('body',['','Preserved original body'])
def test_fixed_existing_archive_rows_byte_unchanged(tmp_path,bounds,cls,body):
    from storage.impl.db_storage import DatabaseStorage
    from models.db import ArticleRecord
    from sqlmodel import Session
    from pipeline.core import DataPipeline
    f=cls();before=dt.datetime.fromisoformat(bounds['publish_before']);stamp=before-dt.timedelta(days=2)
    if cls is HN:
        hn_fixture(f,bounds,[hit(1,stamp)])
        aid=f._entry_id(f.source_id,{'id':'https://news.ycombinator.com/item?id=1'})
        url='https://example.test/article/1'
    elif cls is IT:
        after=dt.datetime.fromisoformat(bounds['publish_after'])
        it_fixture(f,[it_page([stamp]),it_page([after-dt.timedelta(days=1)],1)])
        url='https://www.ithome.com/1/004/000.htm';aid=f._content_id(url)
    else:
        after=dt.datetime.fromisoformat(bounds['publish_after']);zone=ZoneInfo('Asia/Shanghai')
        qbit_fixture(f,[qbit_page([(1,stamp.astimezone(zone).date().isoformat())]),qbit_page([(2,(after-dt.timedelta(days=1)).astimezone(zone).date().isoformat())])],{})
        url='https://www.qbitai.com/2026/11/1.html';aid=f._content_id(url)
    store=DatabaseStorage(db_url=f"sqlite:///{tmp_path/'archive.db'}")
    with Session(store.engine) as session:
        row=ArticleRecord(id=aid,source_id=f.source_id,content_type=f.content_type,title='Untouched title',source_url=url,publish_date='2000-01-01',fetched_date=before.isoformat(),content=body,has_content=bool(body),extensions_json='{"legacy":true}')
        session.add(row);session.commit();original=session.get(ArticleRecord,aid).model_dump()
    result=asyncio.run(DataPipeline([store]).run_task(f,**bounds))
    assert result.fetched_count==0 and result.saved_count==0
    with Session(store.engine) as session:assert session.get(ArticleRecord,aid).model_dump()==original
    reused=[x for x in f.coverage_report.items if x['article_id']==aid]
    assert reused[0]['archive_status']=='reused_archive'
    assert reused[0]['body_status']==('available_unreviewed' if body else 'missing')
    assert f.coverage_report.summary()['complete']


def test_normal_saved_job_run_api_forwards_scope_without_limit_override(monkeypatch,tmp_path,bounds):
    from fastapi.testclient import TestClient
    from models.db import CollectionJobRecord,UserRecord
    from sqlmodel import Session
    from storage.impl.db_storage import DatabaseStorage
    from services.accounts import hash_password
    import api.app as app
    store=DatabaseStorage(db_url=f"sqlite:///{tmp_path/'normal-api.db'}")
    monkeypatch.setattr(app,'db_sink',store)
    captured=[]
    with Session(app.db_sink.engine) as session:
        session.add(UserRecord(username='admin',password_hash=hash_password('fixture-pass'),role='admin',is_active=True,created_at=bounds['publish_before'],updated_at=bounds['publish_before']))
        session.add(CollectionJobRecord(id=1,name='window',created_at=bounds['publish_before'],updated_at=bounds['publish_before'],fetcher_ids_json='["rss_hn_ai"]',params_json='{"limit":20}',per_fetcher_params_json=json.dumps({'rss_hn_ai':bounds})))
        session.commit()
    async def fake_run(items,**kwargs):
        captured.extend(items)
        return {'status':'success','saved_count':0,'failed_count':0,'results':[]}
    monkeypatch.setattr(app,'run_collection_items',fake_run)
    with TestClient(app.app) as client:
        assert client.post('/api/auth/login',json={'username':'admin','password':'fixture-pass'}).status_code==200
        result=client.post('/api/collection-jobs/1/run')
        assert result.status_code==200
        for _ in range(200):
            done=client.get(f"/api/jobs/{result.json()['job_id']}").json()
            if done['status'] in ('succeeded','failed'):break
        assert done['status']=='succeeded'
    assert captured==[{'fetcher_id':'rss_hn_ai','params':bounds}]


@pytest.mark.parametrize('cls',[IT,QB])
def test_web_window_then_legacy_then_window_no_state_leak(bounds,cls):
    f=cls();before=dt.datetime.fromisoformat(bounds['publish_before']);after=dt.datetime.fromisoformat(bounds['publish_after'])
    if cls is IT:
        it_fixture(f,[it_page([before]),it_page([after-dt.timedelta(days=1)],1)])
        f.default_fetch_detail=False
    else:
        zone=ZoneInfo('Asia/Shanghai');stamp=before-dt.timedelta(days=2)
        qbit_fixture(f,[qbit_page([(1,stamp.astimezone(zone).date().isoformat())]),qbit_page([(2,(after-dt.timedelta(days=1)).astimezone(zone).date().isoformat())])],{'1':qbit_detail(stamp)})
    run(f,bounds)
    assert f.coverage_report is not None
    run(f,{'limit':1,'fetch_detail':False})
    assert f.coverage_report is None
    run(f,bounds)
    assert f.coverage_report.summary()['complete']


def test_ithome_naive_date_and_hn_malformed_date_quarantined(bounds):
    f=IT();before=dt.datetime.fromisoformat(bounds['publish_before'])
    it_fixture(f,[it_page([before]).replace('+00:00','')])
    with pytest.raises(RuntimeError,match='date_unknown'):run(f,{**bounds,'fetch_detail':False})
    assert f.coverage_report.items[0]['status']=='date_unknown'
    f=HN();bad=hit(1,before);bad['created_at_i']='not-a-date';hn_fixture(f,bounds,[bad])
    with pytest.raises(RuntimeError,match='api_date_unknown'):run(f,bounds)
    assert f.coverage_report.items[0]['status']=='date_unknown'


def test_qbit_relative_label_requires_detail_even_for_existing_id(bounds):
    f=QB();after=dt.datetime.fromisoformat(bounds['publish_after']);zone=ZoneInfo('Asia/Shanghai')
    qbit_fixture(f,[qbit_page([(1,'昨天 12:00')]),qbit_page([(2,(after-dt.timedelta(days=1)).astimezone(zone).date().isoformat())])],{})
    async def known(ids):return {i:True for i in ids}
    f.dedup_lookup=known
    with pytest.raises(RuntimeError,match='date_unknown'):run(f,bounds)
    row=f.coverage_report.items[0]
    assert row['archive_status']=='reused_archive' and row['status']=='date_unknown'
    assert row['date_proof']['date_precision']=='unknown'
    assert 'date_interval_start' not in row['date_proof']


@pytest.mark.parametrize('cls',[HN,IT])
def test_archive_classification_checks_all_inventory_not_only_selected(bounds,cls):
    f=cls();before=dt.datetime.fromisoformat(bounds['publish_before']);after=dt.datetime.fromisoformat(bounds['publish_after']);queried=[]
    if cls is HN:
        hn_fixture(f,bounds,[hit(1,before)],rss='<rss version="2.0"><channel><item><guid>undated</guid><title>AI</title></item></channel></rss>')
    else:
        it_fixture(f,[it_page([before]),it_page([after-dt.timedelta(days=1)],1)])
    async def existing(ids):
        ids=list(ids);queried.extend(ids)
        return {i:False for i in ids}
    f.dedup_lookup=existing
    if cls is HN:
        with pytest.raises(RuntimeError,match='rss_date_unknown'):run(f,bounds)
    else:
        assert run(f,bounds)==[]
    assert set(queried)=={row['article_id'] for row in f.coverage_report.items}
    assert all(row['archive_status']=='reused_archive' and row['body_status']=='missing' for row in f.coverage_report.items)


def test_qbit_window_preserves_legacy_tags_and_raw_metadata(bounds):
    before=dt.datetime.fromisoformat(bounds['publish_before']);after=dt.datetime.fromisoformat(bounds['publish_after']);zone=ZoneInfo('Asia/Shanghai');stamp=before-dt.timedelta(days=2)
    first=qbit_page([(1,stamp.astimezone(zone).date().isoformat())])
    first=first.replace('<div class="text_box">','<div class="picture"><img data-original="https://img.example.test/cover.jpg"></div><div class="text_box">')
    first=first.replace('<span class="time">','<span class="author">Writer</span><div class="tags_s"><a>模型</a><a>工具</a></div><span class="time">')
    pages=[first,qbit_page([(2,(after-dt.timedelta(days=1)).astimezone(zone).date().isoformat())])]
    legacy=QB();qbit_fixture(legacy,pages,{'1':qbit_detail(stamp)})
    old=run(legacy,{'limit':1})[0]
    scoped=QB();qbit_fixture(scoped,pages,{'1':qbit_detail(stamp)})
    new=run(scoped,bounds)[0]
    assert new.id==old.id and new.source_url==old.source_url and new.content==old.content
    assert new.tags==old.tags==['模型','工具']
    assert all(new.raw_data[k]==v for k,v in old.raw_data.items())
    assert new.raw_data['media_url']=='https://img.example.test/cover.jpg'
    assert new.raw_data['author']=='Writer'


def test_ithome_malformed_emission_survivor_quarantined_without_now(bounds):
    f=IT();before=dt.datetime.fromisoformat(bounds['publish_before']);after=dt.datetime.fromisoformat(bounds['publish_after'])
    it_fixture(f,[it_page([before]),it_page([after-dt.timedelta(days=1)],1)])
    original=f._discover_window_entries
    async def changed(client,window,report):
        entries=await original(client,window,report)
        entries[0][0].select_one('.c')['data-ot']='not-a-source-date'
        return entries
    f._discover_window_entries=changed
    emitted=[]
    async def collect():
        async for item in f._run(None,**bounds):emitted.append(item)
    with pytest.raises(RuntimeError,match='emission_date_invalid'):asyncio.run(collect())
    assert emitted==[] and f.coverage_report.items[0]['status']=='date_unknown'
    assert f.coverage_report.items[0]['date_proof']=={'raw_date':'not-a-source-date','date_precision':'unknown'}


def test_qbit_relative_estimate_cannot_prove_discovery_complete(bounds):
    f=QB();before=dt.datetime.fromisoformat(bounds['publish_before'])
    qbit_fixture(f,[qbit_page([(1,'300小时前')])],{'1':qbit_detail(before-dt.timedelta(days=1))})
    with pytest.raises(RuntimeError,match='page_limit_or_incomplete'):
        run(f,{**bounds,'window_max_pages':1})
    assert f.coverage_report.items[0]['status']=='in_window'
    assert f.coverage_report.discovery_complete is False
