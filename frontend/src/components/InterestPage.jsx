import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Ban, ChevronDown, Loader2, Search, X } from 'lucide-react';
import { fetchInterestCatalog, fetchInterests, saveInterests } from '../api';

/* ── 我的兴趣(issue #23 第二项,弹窗改页面;样页 docs/design/dorami-interest-quiet.html)──
   定位(issue #27 分析):兴趣与合集是阅读偏好的两根正交轴——合集=看谁(源的成员关系,全站生效),
   兴趣=看什么(规范标签的关注/屏蔽,目前只影响个人早报选篇:关注最多占一半名额,屏蔽硬排除)。
   本页只回答两个问题:我能选什么、我选了什么。

   布局与早报页同构:左槽(源栏位)= 选择台账(目录跳转 scrollspy + 关注 n + 屏蔽 n,行尾 × 就地移出);
   右幅(3/-1)= 三面标签目录,报头沿早报报头语法。64 张卡分三节散在一整幅里,滚到实体节时早看不见
   主题节选了什么——台账让「我选了什么」始终在视野里,这是左槽的真实功能。

   交互:整卡一击 = 关注(accent 描边高光,拍板去勾选框——勾选框让人错觉还要下一步);屏蔽是次动作
   (悬停浮出「⊘ 屏蔽」幽灵钮),点了整卡降灰、名称划线、常显「屏蔽中」;不做三态轮转,已屏蔽卡再点主区
   = 解除回中立。保存接口是整套替换并触发当日早报重编,逐点即存会点一下重编一次 → 草稿 + 显式保存:
   任何改动后面底浮出保存条,无改动面底干净。

   首登引导 = 同一页换报头与保存条(不锁页:视图轨照常可走,轨钮挂点直到完成或跳过);引导态每面先取
   热度前 12 个(目录本就按近 30 天热度排序),节尾「展开全部」。每卡「N 篇 / 30 天」= 目录接口现成的
   heat_30d:读者据此知道关注「化工 0 篇」等于没关注,也是 #27「命中 N 篇」扩展的落位。
   移动壳改行式:sticky 三段 seg 顶替目录跳转,台账折成页头下一行。
   页头:报头家族(见下方 kicker 处的规则注释)——目检曾一度改成发现页工具头,用户表态更喜欢衬线,
   于是把差异建立在规则上:「我的」页用报头,容器与目录页用工具头。

   目检返修(2026-09-06 拍板):①保存条取消,**点击即保存**——600ms 合并连点为一次 PUT(每次保存都触发
   当日早报重编,后端重编请求本就是 latest-wins 合并),失败回滚草稿并 Toast;台账头右侧一枚
   faint 小字「保存中… / 已保存」作唯一回执;引导态的「完成 / 稍后再说」挪到报头右侧(选了东西显
   「完成」,空着显「稍后再说」,两者都写 complete_onboarding,已选项一并保留)。②定位句收成一句
   「兴趣内容将在早报中优先呈现」,「去发现」链撤除(冗余、刻意)。③台账行:分面名右对齐,悬停同位置
   换成 ×,不再右留空槽。 */

const KIND_META = {
  topic: { label: '主题', hint: '技术方向与长期议题' },
  industry: { label: '行业', hint: '应用领域与产业方向' },
  entity: { label: '实体', hint: '组织、产品、模型、协议与开源项目' },
};
const KINDS = Object.keys(KIND_META);
const ENTITY_TYPE = { organization: '组织', product: '产品', model: '模型', project: '项目', protocol: '协议' };
const ONBOARDING_PREVIEW = 12;

const keyOf = (tag) => String(tag.id);

function sameStances(a, b) {
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => a[k] === b[k]);
}

function matches(tag, needle) {
  if (!needle) return true;
  return [tag.name_zh, tag.name_en, tag.code, tag.description]
    .some((value) => String(value || '').toLocaleLowerCase().includes(needle));
}

function TagCard({ tag, stance, onToggle, onMute, compact = false }) {
  const cls = `${compact ? 'interest-mrow' : 'interest-card'} ${stance === 'follow' ? 'is-follow' : ''} ${stance === 'mute' ? 'is-mute' : ''}`;
  const kindLabel = tag.kind === 'entity' ? ENTITY_TYPE[tag.entity_type] || '' : '';
  const handleKey = (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      onToggle(tag);
    }
  };
  const banBtn = (
    <button
      type="button"
      className={`interest-card-ban ${compact ? 'is-icon' : ''}`}
      aria-pressed={stance === 'mute'}
      aria-label={stance === 'mute' ? `解除屏蔽 ${tag.name_zh}` : `屏蔽 ${tag.name_zh}`}
      title={stance === 'mute' ? '解除屏蔽' : '屏蔽:含此标签的内容不进早报'}
      onClick={(event) => { event.stopPropagation(); onMute(tag); }}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <Ban aria-hidden="true" />
      {!compact && <span>{stance === 'mute' ? '屏蔽中' : '屏蔽'}</span>}
    </button>
  );
  if (compact) {
    return (
      <div className={cls} role="button" tabIndex={0} aria-pressed={stance === 'follow'} onClick={() => onToggle(tag)} onKeyDown={handleKey}>
        <span className="interest-mrow-body">
          <span className="interest-card-name">{tag.name_zh}</span>
          <span className="interest-card-desc">{tag.description}</span>
        </span>
        {banBtn}
      </div>
    );
  }
  return (
    <div className={cls} role="button" tabIndex={0} aria-pressed={stance === 'follow'} onClick={() => onToggle(tag)} onKeyDown={handleKey}>
      <span className="interest-card-name">{tag.name_zh}</span>
      <span className="interest-card-desc">{tag.description}</span>
      <span className="interest-card-foot">
        {kindLabel && <span className="interest-card-kind">{kindLabel}</span>}
        {banBtn}
      </span>
    </div>
  );
}

// 保存链放模块级(codex 检视 P2):离开页面时的卸载冲刷若仍在途、读者随即重开,新实例的
// 加载与保存都必须排在它之后——per-instance 的链跨不过重挂载,旧冲刷会后到覆盖新选择。
let saveChain = Promise.resolve();
const enqueueSave = (task) => {
  const run = saveChain.then(task);
  saveChain = run.then(() => undefined, () => undefined);
  return run;
};

export default function InterestPage({
  mobile = false,
  onboarding = false,
  // embedded(issue #27 三稿):作为发现页第三段「兴趣」的正文——不画报头与左槽台账,
  // 「我的兴趣」(口径:关注/兴趣统一叫兴趣,2026-09-10)改成目录之上的一行 chip;首登引导 = 顶部一条横幅(不锁页)
  embedded = false,
  // 发现页头部搜索框注入的检索词(v3.52.2):非 null 时嵌入态不画自己的搜索框,与源/合集两段同形
  externalQuery = null,
  onSaved,
  showToast,
}) {
  const [catalog, setCatalog] = useState(null);
  const [draft, setDraft] = useState({});
  const [saveState, setSaveState] = useState('idle'); // idle | saving | saved | error
  const [query, setQuery] = useState('');
  const [expanded, setExpanded] = useState({});
  const [error, setError] = useState('');
  const [reloadKey, setReloadKey] = useState(0);
  const [activeKind, setActiveKind] = useState('topic');
  const [picksOpen, setPicksOpen] = useState(false); // 移动壳台账折叠行
  const sheetRef = useRef(null);
  const sectionRefs = useRef({});
  // ── 点击即保存:草稿变化后 600ms 无新动作即 PUT;卸载时冲刷未发出的那一次 ──
  const savedRef = useRef({});      // 服务端已确认的立场(失败回滚用)
  const draftRef = useRef(draft);   // 供 timer / 卸载冲刷读最新草稿
  const timerRef = useRef(null);
  // 保存串行化(codex 检视 P2):整套替换的 PUT 若并发,后发先至会被先发的旧集覆盖;
  // 所有保存排进一条 promise 链依次发出,并带单调序号——只有最新一次能改本地态,
  // 排队时已有更新一次在后面的过时保存直接跳过(最新那次带的是最新草稿)。
  const seqRef = useRef(0);
  const stateTimerRef = useRef(null);
  const onSavedRef = useRef(onSaved);
  const showToastRef = useRef(showToast);

  useEffect(() => {
    const controller = new AbortController();
    setCatalog(null);
    setError('');
    // 先等在途的保存(含上一实例的卸载冲刷)落定,再读服务端立场——否则读到冲刷前的旧集
    saveChain.then(() => Promise.all([
      fetchInterestCatalog({ signal: controller.signal }),
      fetchInterests({ signal: controller.signal }),
    ])).then(([catalogData, current]) => {
      setCatalog(catalogData);
      // 只收目录里存在的标签(codex 检视 P2):被管理员下架/取消可选的旧选择目录不再返回,
      // 却仍在 /interests 里;带着它整套替换会被后端整次 400,读者什么都改不了。
      // 目录对已选标签「落榜仍保留」,故不在目录里的只可能是失效项,静默剔除。
      const known = new Set((catalogData?.items || []).map((tag) => keyOf(tag)));
      const next = {};
      (current.items || []).forEach(({ tag, stance }) => {
        if (!known.has(keyOf(tag))) return;
        next[keyOf(tag)] = stance === 'mute' ? 'mute' : 'follow';
      });
      setDraft(next);
      savedRef.current = next;
    }).catch((err) => {
      if (err.name !== 'AbortError') setError(err.message || '加载兴趣设置失败，请重试');
    });
    return () => controller.abort();
  }, [reloadKey]);

  useEffect(() => { draftRef.current = draft; }, [draft]);
  useEffect(() => { onSavedRef.current = onSaved; showToastRef.current = showToast; }, [onSaved, showToast]);

  const picks = useMemo(() => {
    const follow = [];
    const mute = [];
    // 目录序(面 → 热度)而非点选序:台账是清单不是历史
    (catalog?.items || []).forEach((tag) => {
      const st = draft[keyOf(tag)];
      if (st === 'follow') follow.push(tag);
      else if (st === 'mute') mute.push(tag);
    });
    return { follow, mute };
  }, [catalog, draft]);

  const effectiveQuery = externalQuery != null ? externalQuery : query;
  const needle = effectiveQuery.trim().toLocaleLowerCase();
  const itemsOf = (stances) => Object.entries(stances).map(([id, stance]) => ({ tag_id: Number(id), stance }));
  const performSave = async (seq, stances, { complete = false, toast = null }) => {
    const latest = () => seq === seqRef.current;
    // 过时的自动保存跳过(后面排着更新的一次);完成引导的那次不跳
    if (!complete && !latest()) return true;
    if (!complete && sameStances(stances, savedRef.current)) return true;
    setSaveState('saving');
    window.clearTimeout(stateTimerRef.current);
    try {
      await saveInterests(itemsOf(stances), { completeOnboarding: complete });
      savedRef.current = stances;
      if (latest()) {
        setSaveState('saved');
        stateTimerRef.current = window.setTimeout(() => setSaveState('idle'), 2200);
      }
      if (toast) showToastRef.current?.(toast, 'success');
      onSavedRef.current?.({ onboardingCompleted: complete });
      return true;
    } catch (err) {
      setSaveState('error');
      // 只在没有更新编辑在途时回滚草稿,否则会把用户后来的改动一起抹掉
      if (latest() && timerRef.current == null && sameStances(draftRef.current, stances)) {
        setDraft(savedRef.current);
        showToastRef.current?.(err.message || '保存兴趣失败，已恢复上次保存的设置', 'error');
      } else {
        showToastRef.current?.(err.message || '保存兴趣失败', 'error');
      }
      return false;
    }
  };
  const commit = useCallback((stances, opts = {}) => {
    const seq = ++seqRef.current;
    return enqueueSave(() => performSave(seq, stances, opts));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const scheduleSave = useCallback(() => {
    window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      timerRef.current = null;
      commit(draftRef.current);
    }, 600);
  }, [commit]);
  useEffect(() => () => {
    // 卸载:未发出的合并保存立即发出(请求不随组件卸载取消)
    if (timerRef.current) {
      window.clearTimeout(timerRef.current);
      const stances = draftRef.current;
      // 排在在途保存之后发出,保持整套替换的先后序
      // 成功后照样通知父级(codex 检视 P2):改完兴趣 600ms 内就切去早报时,这是唯一发出的 PUT,
      // 不通知则 interestVersion 不推进,早报页会停在旧版不去轮询新编排的版本
      enqueueSave(() => {
        if (sameStances(stances, savedRef.current)) return undefined;
        return saveInterests(itemsOf(stances), { completeOnboarding: false }).then(() => {
          savedRef.current = stances;
          onSavedRef.current?.({ onboardingCompleted: false });
        });
      }).catch((err) => {
        // 这是唯一发出的 PUT,失败不能静默——Toast 管道在父级,卸载后仍可用(codex 检视 P2)
        showToastRef.current?.(err?.message || '保存兴趣失败，最后的改动未能保存', 'error');
      });
    }
    window.clearTimeout(stateTimerRef.current);
  }, []);
  const setStance = useCallback((tag, stance) => {
    setDraft((prev) => {
      const next = { ...prev };
      if (stance) next[keyOf(tag)] = stance;
      else delete next[keyOf(tag)];
      return next;
    });
    scheduleSave();
  }, [scheduleSave]);
  // 主区一击:中立 → 关注;关注 → 中立;屏蔽 → 中立(解除)。不做三态轮转。
  const toggleFollow = useCallback((tag) => {
    setStance(tag, draft[keyOf(tag)] ? null : 'follow');
  }, [draft, setStance]);
  const toggleMute = useCallback((tag) => {
    setStance(tag, draft[keyOf(tag)] === 'mute' ? null : 'mute');
  }, [draft, setStance]);

  // 引导态:完成 / 稍后再说 都写 complete_onboarding,已选项一并保留
  const finishOnboarding = async () => {
    window.clearTimeout(timerRef.current); timerRef.current = null;
    const stances = draftRef.current;
    const empty = Object.keys(stances).length === 0;
    await commit(stances, { complete: true, toast: empty ? '已跳过，随时可从「兴趣」回来设置' : '兴趣已保存，今天的早报正在准备' });
  };

  // 目录跳转 + scrollspy:节头越过面顶 96px 即视为当前节
  const jumpTo = (kind) => {
    const el = sectionRefs.current[kind];
    const sheet = sheetRef.current;
    if (!el || !sheet) return;
    sheet.scrollTo({ top: el.offsetTop - 18, behavior: 'smooth' });
    setActiveKind(kind);
  };
  const handleSheetScroll = () => {
    const sheet = sheetRef.current;
    if (!sheet) return;
    let current = KINDS[0];
    KINDS.forEach((kind) => {
      const el = sectionRefs.current[kind];
      if (el && el.offsetTop - sheet.scrollTop <= 96) current = kind;
    });
    setActiveKind((prev) => (prev === current ? prev : current));
  };

  const facets = catalog?.facets || {};
  const sectionOf = (kind) => {
    const all = facets[kind] || [];
    const filtered = needle ? all.filter((tag) => matches(tag, needle)) : all;
    const capped = onboarding && !needle && !expanded[kind] && filtered.length > ONBOARDING_PREVIEW;
    return { all, rows: capped ? filtered.slice(0, ONBOARDING_PREVIEW) : filtered, capped, filtered };
  };

  const loading = !catalog && !error;
  const stateNode = loading ? (
    <div className="brief-state" role="status"><Loader2 className="animate-spin" aria-hidden="true" />正在读取标签目录…</div>
  ) : error && !catalog ? (
    <div className="brief-state is-error" role="alert">
      <span>{error}</span>
      <button type="button" className="interest-btn" onClick={() => setReloadKey((v) => v + 1)}>重试</button>
    </div>
  ) : null;

  // 页头规则(2026-09-06 拍板):**「我的」页用报头,容器与目录页用工具头**——早报与兴趣都以读者本人为
  // 主语、同从视图轨下半区进入、同占源栏槽位 + 整幅右栏,共享等宽 kicker + 衬线标题的报头家族;
  // 文章/社交/发现是内容容器与站内目录,用 14/600 栏名工具头。差异是「不同类」而非「不一致」。
  const kicker = onboarding ? '初始设置 · 欢迎来到哆啦美' : KINDS.map((k) => KIND_META[k].label).join(' / ');
  const hint = onboarding ? '选几个感兴趣的方向，全站相关文章会进入你的阅读器，早报也会优先呈现。' : '感兴趣的方向会把全站相关文章带进阅读器，并在早报里优先呈现。';
  const saveStateNode = saveState !== 'idle' && (
    <span className={`interest-save-state ${saveState === 'error' ? 'is-error' : ''}`} role="status" aria-live="polite">
      {saveState === 'saving' ? '保存中…' : saveState === 'saved' ? '已保存' : '保存失败'}
    </span>
  );
  const picksEmpty = picks.follow.length + picks.mute.length === 0;
  const onboardingBtn = onboarding && catalog && (
    <button type="button" className={`interest-btn ${picksEmpty ? '' : 'is-primary'}`} disabled={saveState === 'saving'} onClick={finishOnboarding}>
      {picksEmpty ? '稍后再说' : '完成'}
    </button>
  );

  const searchNode = (
    <label className="reader-disc-search interest-search">
      <Search aria-hidden="true" />
      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="搜索标签"
        aria-label="搜索标签"
      />
      {query && <button type="button" className="interest-search-clear" aria-label="清空搜索" onClick={() => setQuery('')}><X aria-hidden="true" /></button>}
    </label>
  );

  const pickRow = (tag, stance) => (
    <div key={keyOf(tag)} className={`interest-pick ${stance === 'mute' ? 'is-mute' : ''}`}>
      <span className="interest-pick-dot" aria-hidden="true" />
      <span className="interest-pick-name">{tag.name_zh}</span>
      <span className="interest-pick-end">
        <span className="interest-pick-facet" aria-hidden="true">{KIND_META[tag.kind]?.label}</span>
        <button type="button" className="interest-pick-x" aria-label={`移出 ${tag.name_zh}`} title="移出" onClick={() => setStance(tag, null)}>×</button>
      </span>
    </div>
  );

  // 我的关注 chip 行(嵌入态顶替左槽台账):关注 accent 实点、屏蔽划线空心点,悬停浮出 ×;目录序排列
  const pickChip = (tag, stance) => (
    <span key={keyOf(tag)} className={`interest-chip ${stance === 'mute' ? 'is-mute' : ''}`}>
      <span className="interest-chip-dot" aria-hidden="true" />
      <span className="interest-chip-name">{tag.name_zh}</span>
      <button type="button" className="interest-chip-x" aria-label={`移出 ${tag.name_zh}`} title="移出" onClick={() => setStance(tag, null)}>×</button>
    </span>
  );
  const onboardingBanner = onboarding && catalog && (
    <div className="interest-onb" role="region" aria-label="初始设置">
      <div className="interest-onb-main">
        <div className="brief-mast-kicker">欢迎来到哆啦美</div>
        <p className="interest-onb-text">
          已为你预置了几个来源。在这里选几个<b>感兴趣的方向</b>，全站相关文章会进入你的阅读器，早报也会优先呈现；也可以先去「源」里挑更多来源。现在跳过也可以，随时能回来。
        </p>
      </div>
      {onboardingBtn}
    </div>
  );
  const catalogSections = catalog && KINDS.map((kind) => {
    const sec = sectionOf(kind);
    if (needle && sec.filtered.length === 0) return null;
    return (
      <section key={kind} className="interest-sec" aria-label={KIND_META[kind].label} ref={(el) => { sectionRefs.current[kind] = el; }}>
        <div className="brief-sec-head">
          <span className="brief-sec-title">{KIND_META[kind].label}</span>
          <span className="brief-sec-count">{needle ? `${sec.filtered.length} / ${sec.all.length}` : sec.all.length}</span>
          <span className="interest-sec-hint">{KIND_META[kind].hint}</span>
          <span className="brief-sec-rule" />
        </div>
        <div className="interest-grid">
          {sec.rows.map((tag) => (
            <TagCard key={keyOf(tag)} tag={tag} stance={draft[keyOf(tag)]} onToggle={toggleFollow} onMute={toggleMute} />
          ))}
        </div>
        {sec.capped && (
          <button type="button" className="interest-more" onClick={() => setExpanded((prev) => ({ ...prev, [kind]: true }))}>
            <ChevronDown aria-hidden="true" />展开全部 {sec.all.length} 个{KIND_META[kind].label}
          </button>
        )}
      </section>
    );
  });

  // ── 嵌入态(发现页第三段,桌面):横幅(引导) → 工具行(搜索 + 回执) → 我的关注 chip 行 → 三面目录 ──
  if (embedded && !mobile) {
    const anyMatchEmbed = KINDS.some((k) => sectionOf(k).filtered.length > 0);
    return (
      <div className="interest-embed" aria-label="我的兴趣">
        {onboardingBanner}
        {/* 搜索由发现页头部承担时(externalQuery 注入)不画工具行,「已保存」回执挪到 chip 行右端——
            回执只在保存前后短暂出现,独占一行会让版面上下跳 */}
        {externalQuery == null && (
          <div className="interest-embed-tools">
            {searchNode}
            {saveStateNode}
          </div>
        )}
        {catalog && (
          <div className="interest-picks" aria-label="我的兴趣">
            <span className="interest-picks-label">我的兴趣</span>
            {picksEmpty
              ? <span className="interest-picks-empty">点击下方标签加入兴趣。</span>
              : [...picks.follow.map((t) => pickChip(t, 'follow')), ...picks.mute.map((t) => pickChip(t, 'mute'))]}
            {externalQuery != null && saveStateNode}
          </div>
        )}
        {stateNode}
        {catalogSections}
        {catalog && needle && !anyMatchEmbed && (
          <div className="brief-state">没有匹配「{effectiveQuery.trim()}」的标签</div>
        )}
      </div>
    );
  }

  // ── 移动壳:行式列表 + sticky seg + 折叠台账 + 贴底保存条 ──
  if (mobile) {
    const section = sectionOf(activeKind);
    const searching = Boolean(needle);
    const visibleKinds = searching ? KINDS.filter((k) => sectionOf(k).filtered.length > 0) : [activeKind];
    return (
      <div className={`interest-m ${embedded ? 'is-embedded' : ''}`} aria-label="我的兴趣">
        <div className="interest-m-head">
          {embedded ? onboardingBanner : (
            <>
              <div className="brief-mast-kicker">{kicker}</div>
              <p className="brief-mast-sub">{hint}</p>
            </>
          )}
          {searchNode}
          {catalog && (
            <div className="interest-m-row">
              <button type="button" className="interest-mpicks" aria-expanded={picksOpen} onClick={() => setPicksOpen((v) => !v)}>
                兴趣 <b>{picks.follow.length}</b> · 屏蔽 <b>{picks.mute.length}</b>
                <ChevronDown aria-hidden="true" />
              </button>
              {saveStateNode}
              <span className="interest-m-sp" />
              {!embedded && onboardingBtn}
            </div>
          )}
          {picksOpen && catalog && (
            <div className="interest-mpicks-list">
              {picks.follow.length + picks.mute.length === 0
                ? <div className="interest-ledger-empty">点击下方标签加入兴趣。</div>
                : [...picks.follow.map((t) => pickRow(t, 'follow')), ...picks.mute.map((t) => pickRow(t, 'mute'))]}
            </div>
          )}
        </div>
        {catalog && !searching && (
          <div className="interest-mseg" role="tablist" aria-label="兴趣分面">
            {KINDS.map((kind) => (
              <button key={kind} type="button" role="tab" aria-selected={activeKind === kind} className={activeKind === kind ? 'is-on' : ''} onClick={() => setActiveKind(kind)}>
                {KIND_META[kind].label}<small>{(facets[kind] || []).length}</small>
              </button>
            ))}
          </div>
        )}
        <div className="interest-mlist">
          {stateNode}
          {catalog && visibleKinds.map((kind) => {
            const sec = searching ? sectionOf(kind) : section;
            return (
              <div key={kind} className="interest-mgroup">
                {searching && <div className="brief-sec-head"><span className="brief-sec-title">{KIND_META[kind].label}</span><span className="brief-sec-count">{sec.filtered.length}</span><span className="brief-sec-rule" /></div>}
                {sec.rows.map((tag) => (
                  <TagCard key={keyOf(tag)} tag={tag} stance={draft[keyOf(tag)]} onToggle={toggleFollow} onMute={toggleMute} compact />
                ))}
                {sec.capped && (
                  <button type="button" className="interest-more" onClick={() => setExpanded((prev) => ({ ...prev, [kind]: true }))}>
                    <ChevronDown aria-hidden="true" />展开全部 {sec.all.length} 个{KIND_META[kind].label}
                  </button>
                )}
              </div>
            );
          })}
          {catalog && searching && visibleKinds.length === 0 && (
            <div className="brief-state">没有匹配「{effectiveQuery.trim()}」的标签</div>
          )}
        </div>
      </div>
    );
  }

  // ── 桌面:左槽台账 + 右幅目录面 ──
  const anyMatch = KINDS.some((k) => sectionOf(k).filtered.length > 0);
  return (
    <>
      <aside className="reader-col interest-ledger" aria-label="我的选择">
        <div className="reader-src-head interest-ledger-head"><span className="reader-src-title">我的兴趣</span>{saveStateNode}</div>
        <div className="interest-ledger-scroll">
          <div className="reader-src-label">目录</div>
          {KINDS.map((kind) => (
            <button key={kind} type="button" className={`interest-jump ${activeKind === kind ? 'is-on' : ''}`} aria-pressed={activeKind === kind} onClick={() => jumpTo(kind)}>
              <span className="interest-jump-name">{KIND_META[kind].label}</span>
              <span className="interest-jump-n">{(facets[kind] || []).length || ''}</span>
            </button>
          ))}
          {catalog && (
            <>
              <div className="reader-src-label">兴趣<b>{picks.follow.length}</b></div>
              {picks.follow.length === 0
                ? <div className="interest-ledger-empty">点击右侧标签加入兴趣。</div>
                : picks.follow.map((t) => pickRow(t, 'follow'))}
              {picks.mute.length > 0 && (
                <>
                  <div className="reader-src-label">屏蔽<b>{picks.mute.length}</b></div>
                  {picks.mute.map((t) => pickRow(t, 'mute'))}
                </>
              )}
            </>
          )}
        </div>
      </aside>
      <section className="interest-sheet" aria-label="我的兴趣">
        <div className="interest-body" ref={sheetRef} onScroll={handleSheetScroll}>
        <div className="interest-body-inner">
          <header className="brief-mast">
            <div className="brief-mast-main">
              <div className="brief-mast-kicker">{kicker}</div>
              <h1 className="brief-mast-title">我的兴趣</h1>
              <p className="brief-mast-sub">{hint}</p>
            </div>
            <div className="interest-mast-actions">{searchNode}{onboardingBtn}</div>
          </header>
          {stateNode}
          {catalog && KINDS.map((kind) => {
            const sec = sectionOf(kind);
            if (needle && sec.filtered.length === 0) return null;
            return (
              <section key={kind} className="interest-sec" aria-label={KIND_META[kind].label} ref={(el) => { sectionRefs.current[kind] = el; }}>
                <div className="brief-sec-head">
                  <span className="brief-sec-title">{KIND_META[kind].label}</span>
                  <span className="brief-sec-count">{needle ? `${sec.filtered.length} / ${sec.all.length}` : sec.all.length}</span>
                  <span className="interest-sec-hint">{KIND_META[kind].hint}</span>
                  <span className="brief-sec-rule" />
                </div>
                <div className="interest-grid">
                  {sec.rows.map((tag) => (
                    <TagCard key={keyOf(tag)} tag={tag} stance={draft[keyOf(tag)]} onToggle={toggleFollow} onMute={toggleMute} />
                  ))}
                </div>
                {sec.capped && (
                  <button type="button" className="interest-more" onClick={() => setExpanded((prev) => ({ ...prev, [kind]: true }))}>
                    <ChevronDown aria-hidden="true" />展开全部 {sec.all.length} 个{KIND_META[kind].label}
                  </button>
                )}
              </section>
            );
          })}
          {catalog && needle && !anyMatch && (
            <div className="brief-state">没有匹配「{effectiveQuery.trim()}」的标签</div>
          )}
        </div>
        </div>
      </section>
    </>
  );
}

