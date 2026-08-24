import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownWideNarrow, ArrowLeft, ChevronRight, Loader2, Search } from 'lucide-react';
import LogoMark from './LogoMark';
import { mediaProxyUrl } from '../api';
import { SOURCE_ROLES, sourceRoleOf, platformLabelOf, resolveCompany } from '../sourceTaxonomy';
import { highlightMatch } from '../utils/highlight';
import { renderAnnouncementContent } from '../utils/announcementText';

// last_fetched(ISO)→ 人话:今日 / 昨日 / MM-DD;空值不显示
function lastLabel(lastFetched) {
  const day = String(lastFetched || '').slice(0, 10);
  if (!day) return '';
  const today = new Date();
  const fmt = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  if (day === fmt(today)) return '今日有更新';
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (day === fmt(yesterday)) return '昨日更新';
  return `最近 ${day.slice(5)}`;
}

// 单源卡(源视图分组网格 与 合集详情成员网格 共用同一渲染)。
// showArticleChip:文章源是否也挂「文章」形态标——三形态混排的上下文(全部视图/
// 合集详情)才标,已按形态过滤时不标(每卡同词是纯噪声)。
function SourceCard({ source, subbed, pinning, query, showArticleChip, onSubscribe, onUnsubscribe, onPreview }) {
  const q = (query || '').trim();
  return (
    <div className="reader-disc-card">
      <div className="reader-disc-card-main">
        {source.avatar_url ? (
          <img className="reader-disc-avatar" src={mediaProxyUrl(source.avatar_url)} alt="" loading="lazy" decoding="async" />
        ) : (
          <LogoMark company={resolveCompany(source)} size="s34" emoji={source.icon} />
        )}
        <div className="reader-disc-card-mid">
          <div className="reader-disc-name-row">
            <span className="reader-disc-name">{q ? highlightMatch(source.name || source.source_id, q) : (source.name || source.source_id)}</span>
            {(source.shape || 'article') === 'article' && showArticleChip && (
              <span className="reader-shape-chip">文章</span>
            )}
            {(source.shape || 'article') === 'bulletin' && (
              <span className="reader-shape-chip">动态</span>
            )}
            {(source.shape || 'article') === 'social' && (
              <span className="reader-shape-chip">{platformLabelOf(source.platform)}</span>
            )}
          </div>
          {source.description && (
            <p className="reader-disc-desc">{q ? highlightMatch(source.description, q) : source.description}</p>
          )}
          {/* 收录篇数对选源无甄别力,让位给订阅人数(社会证明);无人订阅时给「暂无订阅」 */}
          <p className="reader-disc-meta">
            {(source.subscriber_count || 0) > 0
              ? `${(source.subscriber_count || 0).toLocaleString()} 人订阅`
              : '暂无订阅'}
            {source.last_fetched ? ` · ${lastLabel(source.last_fetched)}` : ''}
          </p>
        </div>
        <div className="reader-disc-card-side">
          <button
            type="button"
            className={`reader-disc-sub ${subbed ? 'is-subbed' : ''}`}
            disabled={pinning}
            onClick={() => (subbed ? onUnsubscribe(source) : onSubscribe(source))}
          >
            {pinning
              ? <Loader2 className="h-3 w-3 animate-spin" />
              : subbed
                ? <><span className="t-on">✓ 已订阅</span><span className="t-off">取消订阅</span></>
                : '订阅'}
          </button>
          <button
            type="button"
            className="reader-disc-prev"
            title={`查看 ${source.name || source.source_id} 的收录列表`}
            onClick={() => onPreview?.(source)}
          >
            <ChevronRight className="h-[11px] w-[11px]" aria-hidden="true" />
            预览
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * 发现页(参照 Folo,按设计样页):取代源栏的「发现更多来源」内联子列表,
 * 占据 条目列+阅读窗 的整片区域。目录=全站可订阅来源(含已订阅,卡上呈订阅态),
 * 按编辑分层分组、双列卡片;形态分段 + 目录搜索过滤。
 * 「预览」= Folo 语义:直接跳转到该源的条目列表(onPreview → goSource,退出发现页),
 * 未订阅的源同样可看——列表接口本就不按订阅收窄。目录数据全部来自
 * GET /api/reader/sources(count/last_fetched/subscriber_count)。
 *
 * 源合集(策展合集,docs/source-collections-wave-plan.md):头部「源 ⇄ 合集」二段
 * seg 切换目录视图。合集=目录呈现层的批量动作而非订阅实体——合集卡上的订阅按钮
 * 三态(订阅全部/订阅其余 k 个/已全部订阅→退订),退订走两击确认(首点入 4s 待确认态,
 * 防呆同轨底退出;批量退订会退掉同属其它合集的源,title 如实列出成员)。
 * 合集详情态提升在 useReaderState(activeCollectionId)——移动端要把详情注册为
 * 独立返回键历史层。成员卡数据由 sources 目录就地 join(隐藏源已被上游滤除,
 * join 不到的成员自然不渲染,与后端批量端点的 unavailable 口径一致)。
 */
export default function DiscoverPage({
  sources,
  subscribedIds,
  loading = false,
  pinningId = null,
  onSubscribe,
  onUnsubscribe,
  onPreview,
  collections = [],
  activeCollectionId = null,
  onOpenCollection,
  onCloseCollection,
  collectionPinningId = null,
  onSubscribeCollection,
  onUnsubscribeCollection,
}) {
  const [tab, setTab] = useState('sources'); // sources | collections
  const [shape, setShape] = useState('all');   // all | article | bulletin | social
  const [query, setQuery] = useState('');
  // 排序小开关:默认(收录量降序,原有秩序)⇄ 订阅降序(全站订阅人数,选源社会证明)
  const [sortBySubs, setSortBySubs] = useState(false);
  // 退订合集的两击确认态(4s 自动回落;确认语义见上方组件注释)
  const [confirmingId, setConfirmingId] = useState(null);
  const confirmTimerRef = useRef(null);
  useEffect(() => () => clearTimeout(confirmTimerRef.current), []);

  // 分组统一「信息角色」单轴;形态(文章/动态/社交)交给上方过滤条,不作分组维度。
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const buckets = {};
    for (const s of sources) {
      const sShape = s.shape || 'article';
      if (shape !== 'all' && sShape !== shape) continue;
      if (q && !`${s.name || ''} ${s.description || ''} ${s.source_id}`.toLowerCase().includes(q)) continue;
      (buckets[sourceRoleOf(s)] ||= []).push(s);
    }
    for (const key of Object.keys(buckets)) {
      buckets[key].sort((a, b) => (sortBySubs
        ? (b.subscriber_count || 0) - (a.subscriber_count || 0) || (b.count || 0) - (a.count || 0)
        : (b.count || 0) - (a.count || 0)));
    }
    return SOURCE_ROLES
      .map((r) => ({ ...r, list: buckets[r.key] || [] }))
      .filter((g) => g.list.length > 0);
  }, [sources, shape, query, sortBySubs]);

  // 合集视图模型:成员与订阅计数由 sources 目录 join(隐藏源上游已滤,join 不到即不渲染)
  const sourceById = useMemo(() => {
    const map = new Map();
    for (const s of sources) map.set(s.source_id, s);
    return map;
  }, [sources]);

  const collectionsView = useMemo(() => collections.map((c) => {
    const members = (c.source_ids || []).map((id) => sourceById.get(id)).filter(Boolean);
    const subCount = members.filter((s) => subscribedIds.has(s.source_id)).length;
    return { ...c, members, subCount };
  }), [collections, sourceById, subscribedIds]);

  const filteredCollections = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return collectionsView;
    return collectionsView.filter((c) => (
      `${c.name} ${c.description} ${c.members.map((m) => m.name || m.source_id).join(' ')}`.toLowerCase().includes(q)
    ));
  }, [collectionsView, query]);

  const activeCollection = activeCollectionId
    ? collectionsView.find((c) => c.collection_id === activeCollectionId) || null
    : null;

  const disarmConfirm = () => {
    clearTimeout(confirmTimerRef.current);
    setConfirmingId(null);
  };

  const handleCollectionButton = (c) => {
    if (c.members.length === 0) return;
    if (c.subCount < c.members.length) {
      disarmConfirm();
      onSubscribeCollection?.(c);
      return;
    }
    // 已全部订阅 → 退订走两击确认(首点入待确认态,4s 未再点自动回落)
    if (confirmingId === c.collection_id) {
      disarmConfirm();
      onUnsubscribeCollection?.(c);
    } else {
      clearTimeout(confirmTimerRef.current);
      setConfirmingId(c.collection_id);
      confirmTimerRef.current = setTimeout(() => setConfirmingId(null), 4000);
    }
  };

  // 合集批量按钮(合集卡与详情头共用):三态 + 退订确认;title 列出将退订的成员
  const renderCollectionButton = (c) => {
    const total = c.members.length;
    const pinning = collectionPinningId === c.collection_id;
    const allSubbed = total > 0 && c.subCount === total;
    const confirming = confirmingId === c.collection_id;
    const memberNames = c.members.map((m) => m.name || m.source_id).join('、');
    return (
      <button
        type="button"
        className={`reader-disc-sub reader-disc-coll-sub ${allSubbed && !confirming ? 'is-subbed' : ''} ${confirming ? 'is-confirm' : ''}`}
        disabled={pinning || total === 0}
        title={allSubbed ? `退订将移除:${memberNames}` : `订阅:${memberNames}`}
        onClick={(e) => { e.stopPropagation(); handleCollectionButton(c); }}
      >
        {pinning
          ? <Loader2 className="h-3 w-3 animate-spin" />
          : total === 0
            ? '暂不可用'
            : confirming
              ? `确认退订 ${total} 个源?`
              : allSubbed
                ? <><span className="t-on">✓ 已全部订阅</span><span className="t-off">退订合集</span></>
                : c.subCount > 0
                  ? `订阅其余 ${total - c.subCount} 个`
                  : `订阅全部 (${total})`}
      </button>
    );
  };

  // 成员头像堆叠(合集卡):前 5 个真实头像/LogoMark 叠排 + 余量计数
  const renderAvatarStack = (c) => (
    <div className="reader-disc-coll-avatars" aria-hidden="true">
      {c.members.slice(0, 5).map((s) => (
        <span key={s.source_id} className="reader-disc-coll-ava">
          {s.avatar_url
            ? <img src={mediaProxyUrl(s.avatar_url)} alt="" loading="lazy" decoding="async" />
            : <LogoMark company={resolveCompany(s)} size="xs" emoji={s.icon} />}
        </span>
      ))}
      {c.members.length > 5 && <span className="reader-disc-coll-more">+{c.members.length - 5}</span>}
    </div>
  );

  const inDetail = Boolean(activeCollection);

  return (
    <main className="reader-disc" aria-label="发现">
      <div className="reader-disc-head">
        <div className="reader-disc-head-inner">
          {inDetail ? (
            /* ── 合集详情头:返回 + 合集元信息 + 批量按钮 ── */
            <div className="reader-disc-coll-dethead">
              <button
                type="button"
                className="reader-disc-back"
                onClick={() => { disarmConfirm(); onCloseCollection?.(); }}
              >
                <ArrowLeft className="h-[13px] w-[13px]" aria-hidden="true" />
                合集
              </button>
              <div className="reader-disc-coll-detmain">
                <div className="reader-disc-coll-detrow">
                  <span className="reader-disc-title">{activeCollection.name}</span>
                  <span className="reader-disc-coll-count">
                    {activeCollection.members.length} 源 · 已订阅 {activeCollection.subCount}
                  </span>
                  <span className="reader-disc-coll-detact">{renderCollectionButton(activeCollection)}</span>
                </div>
                {activeCollection.description && (
                  <p className="reader-disc-coll-desc">{activeCollection.description}</p>
                )}
                {activeCollection.provenance_note && (
                  <p className="reader-disc-coll-note">{renderAnnouncementContent(activeCollection.provenance_note)}</p>
                )}
              </div>
            </div>
          ) : (
            <>
              <div className="reader-disc-title-row">
                <span className="reader-disc-title">发现</span>
                <span className="reader-disc-hint">
                  {tab === 'collections' ? '按主题策展的来源合集,一键整组订阅' : '浏览全站收录的来源,一键订阅到你的阅读器'}
                </span>
              </div>
              <div className="reader-disc-tools">
                {/* 目录视图切换:平铺源目录 ⇄ 策展合集 */}
                <span className="reader-seg reader-disc-viewseg" role="group" aria-label="目录视图">
                  {[['sources', '源'], ['collections', '合集']].map(([key, label]) => (
                    <button
                      key={key}
                      type="button"
                      className={`reader-seg-btn ${tab === key ? 'is-on' : ''}`}
                      onClick={() => { setTab(key); disarmConfirm(); }}
                    >
                      {label}
                    </button>
                  ))}
                </span>
                <label className="reader-disc-search">
                  <Search className="h-[13px] w-[13px]" aria-hidden="true" />
                  <input
                    type="search"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={tab === 'collections' ? '筛选合集…' : '筛选来源名称或简介…'}
                    aria-label={tab === 'collections' ? '筛选合集' : '筛选来源'}
                  />
                </label>
                {tab === 'sources' && (
                  <>
                    <span className="reader-seg reader-disc-seg" role="group" aria-label="形态筛选">
                      {[['all', '全部'], ['article', '文章'], ['bulletin', '动态'], ['social', '社交']].map(([key, label]) => (
                        <button
                          key={key}
                          type="button"
                          className={`reader-seg-btn ${shape === key ? 'is-on' : ''}`}
                          onClick={() => setShape(key)}
                        >
                          {label}
                        </button>
                      ))}
                    </span>
                    {/* 排序小开关(icon-only ghost):点击在「默认排序 ⇄ 订阅降序」间切换
                        (组内排序,不打散角色分组);状态靠点亮态 + tooltip 表达 */}
                    <button
                      type="button"
                      className={`reader-disc-sort ${sortBySubs ? 'is-on' : ''}`}
                      aria-pressed={sortBySubs}
                      aria-label={sortBySubs ? '订阅降序(点击切回默认排序)' : '默认排序(点击切换为订阅降序)'}
                      title={sortBySubs ? '订阅降序 · 点击切回默认排序' : '默认排序 · 点击按订阅人数降序'}
                      onClick={() => setSortBySubs((v) => !v)}
                    >
                      <ArrowDownWideNarrow className="h-[14px] w-[14px]" aria-hidden="true" />
                    </button>
                  </>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      <div className="reader-disc-scroll">
        <div className="reader-disc-body">
          {loading ? (
            <div className="reader-disc-empty">目录加载中…</div>
          ) : inDetail ? (
            /* ── 合集详情:成员源卡网格(与源视图同一张卡,单源仍可独立订阅/预览) ── */
            activeCollection.members.length === 0 ? (
              <div className="reader-disc-empty">该合集的来源暂不可用</div>
            ) : (
              <div className="reader-disc-grid">
                {activeCollection.members.map((source) => (
                  <SourceCard
                    key={source.source_id}
                    source={source}
                    subbed={subscribedIds.has(source.source_id)}
                    pinning={pinningId === source.source_id}
                    query=""
                    showArticleChip
                    onSubscribe={onSubscribe}
                    onUnsubscribe={onUnsubscribe}
                    onPreview={onPreview}
                  />
                ))}
              </div>
            )
          ) : tab === 'collections' ? (
            /* ── 合集列表:策展卡(整卡可点进详情;按钮区 stopPropagation) ── */
            filteredCollections.length === 0 ? (
              <div className="reader-disc-empty">{collections.length === 0 ? '暂无合集' : '没有匹配的合集'}</div>
            ) : (
              <div className="reader-disc-coll-grid">
                {filteredCollections.map((c) => (
                  <div
                    key={c.collection_id}
                    className="reader-disc-card reader-disc-coll-card"
                    role="button"
                    tabIndex={0}
                    onClick={() => { disarmConfirm(); onOpenCollection?.(c); }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        disarmConfirm();
                        onOpenCollection?.(c);
                      }
                    }}
                  >
                    <div className="reader-disc-coll-toprow">
                      {renderAvatarStack(c)}
                      {renderCollectionButton(c)}
                    </div>
                    <div className="reader-disc-name-row">
                      <span className="reader-disc-name">{query.trim() ? highlightMatch(c.name, query.trim()) : c.name}</span>
                      <span className="reader-disc-coll-count">{c.members.length} 源{c.subCount > 0 ? ` · 已订阅 ${c.subCount}` : ''}</span>
                    </div>
                    {c.description && <p className="reader-disc-desc">{c.description}</p>}
                    {c.provenance_note && (
                      <p className="reader-disc-coll-note">{renderAnnouncementContent(c.provenance_note)}</p>
                    )}
                  </div>
                ))}
              </div>
            )
          ) : groups.length === 0 ? (
            <div className="reader-disc-empty">没有匹配的来源</div>
          ) : (
            groups.map(({ key, label, list }) => (
              <section key={key}>
                <div className="reader-disc-grp">
                  <span className="reader-src-label reader-disc-grp-label">{label}</span>
                  <span className="reader-disc-grp-n">{list.length}</span>
                </div>
                <div className="reader-disc-grid">
                  {list.map((source) => (
                    <SourceCard
                      key={source.source_id}
                      source={source}
                      subbed={subscribedIds.has(source.source_id)}
                      pinning={pinningId === source.source_id}
                      query={query}
                      showArticleChip={shape === 'all'}
                      onSubscribe={onSubscribe}
                      onUnsubscribe={onUnsubscribe}
                      onPreview={onPreview}
                    />
                  ))}
                </div>
              </section>
            ))
          )}
        </div>
      </div>
    </main>
  );
}
