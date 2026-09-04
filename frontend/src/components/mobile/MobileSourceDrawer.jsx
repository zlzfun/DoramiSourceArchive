import { FileText, Zap, AtSign, Podcast, Star, Compass } from 'lucide-react';
import LogoMark from '../LogoMark';
import { resolveCompany } from '../../sourceTaxonomy';
import { mediaProxyUrl } from '../../api';
import { useLongPress } from '../../hooks/useLongPress';

// 源抽屉(移动波 Wave2,样页画面④):桌面源栏的移动翻译——选源是作用在当前容器上的
// **过滤器**而非目的地(§4.1.2),故从条目流顶栏左钮滑入、不占底部 Tab。
// 聚合入口(全部XX=品牌靛/只看收藏=琥珀星)、信息角色分组、未读加粗全部照搬源栏语法;
// 桌面 hover 退订减号的触屏等价 = 长按源行弹动作单(全部标读/打开原站/退订)。
export default function MobileSourceDrawer({
  open,
  onClose,
  mode,
  socialView,
  sourcesLoading,
  sidebarGroups,
  unreadBySource,
  activeSourceId,
  favOnly,
  hasNoSubscriptions,
  activeUnsubscribed,
  sheetAnchorKey = null,
  goContainerAll,
  goFavorites,
  goSource,
  onOpenDiscover,
  onSourcePress,
}) {
  const pressBind = useLongPress((source) => onSourcePress?.(source));

  if (!open) return null;

  const allLabel = mode === 'bulletin' ? '全部动态' : socialView ? '全部社媒' : mode === 'podcast' ? '全部播客' : '全部文章';
  const AllIcon = mode === 'bulletin' ? Zap : socialView ? AtSign : mode === 'podcast' ? Podcast : FileText;
  const pick = (fn) => (...args) => { fn(...args); onClose?.(); };

  return (
    <div className="m-drawer-layer" role="presentation">
      <div className="m-dim" onClick={onClose} aria-hidden="true" />
      <aside className="m-drawer" aria-label="订阅源">
        <div className="m-drawer-title">我的订阅</div>
        <div className="m-drawer-scroll">
          {/* 预览锚点行(Folo):正在预览的未订阅源浮现在顶部,交代「你在哪」 */}
          {activeUnsubscribed && (
            <div className="reader-source-row reader-source-row-active">
              <LogoMark company={resolveCompany(activeUnsubscribed)} size="s20" emoji={activeUnsubscribed.icon} />
              <p className="reader-source-name min-w-0 flex-1">{activeUnsubscribed.name || activeUnsubscribed.source_id}</p>
              <span className="reader-src-preview-tag">预览</span>
            </div>
          )}

          <button
            type="button"
            onClick={pick(goContainerAll)}
            className={`m-agg-row ${activeSourceId === null && !favOnly ? 'is-on' : ''}`}
          >
            <AllIcon aria-hidden="true" />
            <span>{allLabel}</span>
          </button>
          <button
            type="button"
            onClick={pick(goFavorites)}
            className={`m-agg-row m-agg-fav ${favOnly ? 'is-on' : ''}`}
          >
            <Star aria-hidden="true" fill={favOnly ? 'currentColor' : 'none'} />
            <span>只看收藏</span>
          </button>

          {sourcesLoading ? (
            <div className="m-drawer-skel" aria-hidden="true">
              {[0, 1, 2, 3, 4].map((i) => (
                <div key={i} className="flex items-center gap-2.5 px-2.5 py-2">
                  <div className="skeleton h-5 w-5 rounded-[var(--r-sm)]" />
                  <div className={`skeleton h-3.5 ${['w-3/4', 'w-2/3', 'w-4/5', 'w-1/2', 'w-3/5'][i]}`} />
                </div>
              ))}
            </div>
          ) : (
            <>
              {sidebarGroups.map(({ key, label, list }) => (
                <section key={key}>
                  <div className="reader-src-label">{label}</div>
                  {list.map((source) => {
                    const active = activeSourceId === source.source_id;
                    const unread = unreadBySource[source.source_id] || 0;
                    return (
                      <div
                        key={source.source_id}
                        className="m-press"
                        {...pressBind(source)}
                      >
                        <div
                          role="button"
                          tabIndex={0}
                          onClick={() => { goSource(source.source_id); onClose?.(); }}
                          onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goSource(source.source_id); onClose?.(); } }}
                          className={`reader-source-row ${active ? 'reader-source-row-active' : ''} ${unread > 0 ? 'has-unread' : ''} ${source.hidden ? 'is-unavailable' : ''} ${sheetAnchorKey === `source:${source.source_id}` ? 'is-ctx-anchor' : ''}`}
                        >
                          {source.avatar_url ? (
                            <img className="reader-src-avatar" src={mediaProxyUrl(source.avatar_url)} alt="" loading="lazy" decoding="async" />
                          ) : (
                            <LogoMark company={resolveCompany(source)} size="s20" emoji={source.icon} />
                          )}
                          <p className="reader-source-name min-w-0 flex-1">{source.name || source.source_id}</p>
                          {source.hidden && <span className="reader-src-off">暂不可用</span>}
                        </div>
                      </div>
                    );
                  })}
                </section>
              ))}
              {hasNoSubscriptions && (
                <p className="reader-side-hint">还没有订阅任何来源，在「发现」页挑选并添加。</p>
              )}
            </>
          )}
        </div>
        <div className="m-drawer-foot">
          <button type="button" className="m-drawer-disc" onClick={pick(onOpenDiscover)}>
            <Compass aria-hidden="true" />
            <span>发现更多来源</span>
          </button>
        </div>
      </aside>
    </div>
  );
}
