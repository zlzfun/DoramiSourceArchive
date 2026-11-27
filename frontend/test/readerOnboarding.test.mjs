import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const source = readFileSync(new URL('../src/hooks/useReaderState.js', import.meta.url), 'utf8');
const between = (a,b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
const tick = () => new Promise((resolve) => setImmediate(resolve));
const same = (a,b) => a && b && a.length === b.length && a.every((v,i) => Object.is(v,b[i]));
// Execute the actual async loaders and redirect effect. Transport ignores abort
// deliberately, so old-owner requests must be fenced by sequence and owner key.
function harness() {
  const slots=[]; let cursor=0; let effects=[];
  const useState=(initial) => { const i=cursor++; if (!(i in slots)) slots[i]=typeof initial==='function'?initial():initial; return [slots[i],v=>{ slots[i]=typeof v==='function'?v(slots[i]):v; }]; };
  const useRef=(initial) => { const i=cursor++; return slots[i]??={current:initial}; };
  const useCallback=(fn,deps) => { const i=cursor++; if (!same(slots[i]?.deps,deps)) slots[i]={fn,deps}; return slots[i].fn; };
  const useEffect=(fn,deps) => { const i=cursor++; if (!same(slots[i]?.deps,deps)) {const old=slots[i]; slots[i]={deps};effects.push(()=>{old?.cleanup?.();slots[i].cleanup=fn();});} };
  const pending={sources:[], interests:[]};const nav=[];
  const fetcher=(key)=>()=>new Promise((resolve,reject)=>pending[key].push({resolve,reject}));
  const bindings={useState,useRef,useCallback,useEffect,fetchReaderSources:fetcher('sources'),fetchInterests:fetcher('interests'),
    canFilterCustomInterests:()=>true,resolveNavigation:()=>null,showToast:()=>{},nav};
  const body=`return function render({sessionKey,interestAxisEnabled=true,isGuest=false,initialArticleId=''}) {
    ${between('  const [sources, setSources]', '  const [activeSourceId')}
    const activeCustomInterestId=''; const openSeqRef=useRef(0);
    ${between('  const [customInterests, setCustomInterests]', '  // 兴趣轴分组')}
    ${between('  const supersedePendingOpen =', '  const openDiscover =')}
    const openDiscover=useCallback(()=>{supersedePendingOpen(); nav.push('discover');},[supersedePendingOpen]);
    ${between('  const loadSources =', '  // 合集目录')}
    const hasNoSubscriptions=!sourcesLoading && subscribedIds.size===0;
    ${between('  // 同一会话的两个目录', '  // ── hover')}
    return {loadSources,refreshInterests,supersedePendingOpen,sources,customInterests,hasInterests};
  };`;
  const render=new Function(...Object.keys(bindings),body)(...Object.values(bindings));
  return {pending,nav,render(props={sessionKey:'alice:1'}){cursor=0;effects=[];const result=render(props);effects.forEach(f=>f());return result;}};
}
const emptySources={sources:[],subscribed_source_ids:[]};

test('slow interest response never redirects an interest-only account',async()=>{
  const h=harness();h.render();h.pending.sources[0].resolve(emptySources);await tick();h.render();assert.deepEqual(h.nav,[]);
  h.pending.interests[0].resolve({items:[],custom_items:[{id:7,status:'active'}]});await tick();h.render();assert.deepEqual(h.nav,[]);
});

test('confirmed empty onboarding runs once, subscription or public interest prevents it',async()=>{
  for (const kind of ['empty','source','public','disabled','guest']) {
    const h=harness();const props={sessionKey:'alice:1',interestAxisEnabled:kind!=='disabled',isGuest:kind==='guest'};
    h.render(props);h.pending.sources[0].resolve(kind==='source'?{sources:[],subscribed_source_ids:['s']}:emptySources);
    h.pending.interests[0]?.resolve(kind==='public'?{items:[{tag:{id:1,name_zh:'AI'}}]}:{items:[],custom_items:[]});await tick();h.render(props);
    const expected=['empty','disabled','guest'].includes(kind)?['discover']:[];assert.deepEqual(h.nav,expected);
    const r=h.render(props);r.loadSources();h.pending.sources[1].resolve(emptySources);await tick();h.render(props);assert.deepEqual(h.nav,expected);
  }
});

test('errors, late old-owner results and aborted responses never mean empty interests',async()=>{
  const h=harness();h.render();h.pending.sources[0].resolve(emptySources);h.pending.interests[0].reject(new Error('temporary'));await tick();h.render();assert.deepEqual(h.nav,[]);
  h.render({sessionKey:'bob:2'});h.pending.sources[1].resolve(emptySources);await tick();h.render({sessionKey:'bob:2'});assert.deepEqual(h.nav,[]);
  h.render({sessionKey:'carol:3'});h.pending.sources[2].resolve(emptySources);h.pending.interests[2].resolve({custom_items:[{id:8,status:'active'}]});await tick();h.render({sessionKey:'carol:3'});
  h.pending.interests[1].resolve({custom_items:[]});await tick();const r=h.render({sessionKey:'carol:3'});assert.equal(r.customInterests[0].id,8);assert.deepEqual(h.nav,[]);
});

test('explicit navigation and deep links survive background initialization',async()=>{
  for (const deep of [false,true]) {
    const h=harness();const props={sessionKey:'alice:1',initialArticleId:deep?'article':''};const r=h.render(props);
    if (!deep) r.supersedePendingOpen();
    h.pending.sources[0].resolve(emptySources);h.pending.interests[0].resolve({custom_items:[]});await tick();h.render(props);assert.deepEqual(h.nav,[]);
  }
});
