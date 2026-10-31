import { ChevronRight, Compass, Inbox, Lock, Star, Tags } from 'lucide-react';
import LogoMark from '../LogoMark';
import { resolveCompany } from '../../sourceTaxonomy';
import { mediaProxyUrl } from '../../api';
import { useLongPress } from '../../hooks/useLongPress';

// 订阅 Tab 的正文(issue #230 阶段 3 第 2 批的最小可用版;样页「订阅」「兴趣」两屏):
// 页头转盘 订阅 / 兴趣 在 MobileReader 里,这里只画正文。
//   订阅段 =「全部」「收藏」两个汇总入口 + 三种形态合一张表的订阅(拍板 25)+「发现更多来源」;长按来源行出动作单(全部标读 / 打开原站 / 退订)。
//   兴趣段 = 按兴趣读(兴趣轴与单个兴趣)+ 现网 InterestPage 的移动档(点击即保存、搜索、主题 / 行业 / 实体)。
// 过渡形态:点来源 / 全部 / 收藏 / 兴趣 = 切回「内容」Tab 并带上对应筛选(原源抽屉的语义);
// 第 3a 批改成在订阅 Tab 里推入它自己的条目流(第二个列表实例,不动内容 Tab)。
export default function MobileSubsPage({
  seg = 'subs',
  guest = false,
  onGuestLogin,
  sourcesLoading,
  groups,
  unreadBySource,
  subscribedCount,
  favoriteCount,
  hasNoSubscriptions,
  sheetAnchorKey = null,
  onAll,
  onFavorites,
  onSource,
  onSourcePress,
  onDiscover,
  // 兴趣段
  hasInterests = false,
  interestGroups = [],
  onInterestAxis,
  onTag,
  interestPanel = null,
}) {
  const pressBind = useLongPress((source) => onSourcePress?.(source));

  if (seg === 'interest') {
    return (
      <div className="m-subs-scroll m-subs-int">
        {guest ? (
          <div className="m-note">
            <p>登录后可以设置兴趣：兴趣内容将在早报中优先呈现</p>
            <button type="button" className="action-button action-button-primary m-note-btn" onClick={onGuestLogin}>登录</button>
          </div>
        ) : (
          <>
            {hasInterests && (
              <section aria-label="按兴趣读">
                <p className="m-group-label">按兴趣读</p>
                <div className="m-group">
                  <button type="button" className="m-row is-plain" onClick={onInterestAxis}>
                    <Tags aria-hidden="true" />
                    <span className="m-row-label">全部兴趣内容</span>
                    <span className="m-row-meta">全站</span>
                    <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
                  </button>
                  {interestGroups.flatMap(({ key, label, list }) => list.map((tag) => (
                    <button key={`${key}:${tag.id}`} type="button" className="m-row is-plain m-row-tag" onClick={() => onTag?.(tag.id)}>
                      <span className="m-tagdot" aria-hidden="true" />
                      <span className="m-row-label">{tag.name}</span>
                      <span className="m-row-meta">{label}</span>
                      <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
                    </button>
                  )))}
                </div>
              </section>
            )}
            {interestPanel}
          </>
        )}
      </div>
    );
  }

  return (
    <div className="m-subs-scroll">
      {guest && (
        <div className="m-note">
          <p>游客看到的是默认订阅，登录后可以增删来源、收藏、设置兴趣</p>
          <button type="button" className="action-button action-button-primary m-note-btn" onClick={onGuestLogin}>登录</button>
        </div>
      )}
      <div className="m-group">
        <button type="button" className="m-row is-plain" onClick={onAll}>
          <Inbox aria-hidden="true" />
          <span className="m-row-label">全部</span>
          <span className="m-row-meta">{subscribedCount} 个来源</span>
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
        <button type="button" className="m-row is-plain" onClick={onFavorites}>
          <Star className="is-star" aria-hidden="true" />
          <span className="m-row-label">收藏</span>
          {guest
            ? <Lock className="m-row-lock" aria-label="需要登录" />
            : favoriteCount > 0 && <span className="m-row-meta">{favoriteCount}</span>}
          <span className="m-row-chev" aria-hidden="true"><ChevronRight /></span>
        </button>
      </div>

      {sourcesLoading ? (
        <div className="m-group m-subs-skel" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="m-src-row">
              <div className="skeleton h-5 w-5 rounded-[var(--r-sm)]" />
              <div className={`skeleton h-3.5 ${['w-3/4', 'w-2/3', 'w-4/5', 'w-1/2'][i]}`} />
            </div>
          ))}
        </div>
      ) : groups.map(({ key, label, list }) => (
        <section key={key} aria-label={label}>
          <p className="m-group-label">{label}</p>
          <div className="m-group">
            {list.map((source) => {
              const unread = unreadBySource[source.source_id] || 0;
              const anchor = sheetAnchorKey === `source:${source.source_id}`;
              return (
                <div key={source.source_id} className="m-press" {...pressBind(source)}>
                  <button
                    type="button"
                    className={`m-src-row ${unread > 0 ? 'has-unread' : ''} ${source.hidden ? 'is-off' : ''} ${anchor ? 'is-ctx-anchor' : ''}`}
                    onClick={() => onSource?.(source.source_id)}
                  >
                    {source.avatar_url ? (
                      <img className="m-src-avatar" src={mediaProxyUrl(source.avatar_url)} alt="" loading="lazy" decoding="async" draggable={false} />
                    ) : (
                      <LogoMark company={resolveCompany(source)} size="xs" emoji={source.icon} />
                    )}
                    {/* 与源栏同口径:不显示未读数,有无未读只靠行加粗 + 读屏「有未读」 */}
                    <span className="m-src-name">{source.name || source.source_id}</span>
                    {unread > 0 && <span className="sr-only">，有未读</span>}
                    {source.hidden && <span className="m-src-off">暂不可用</span>}
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      ))}
      {hasNoSubscriptions && (
        <p className="m-subs-hint">还没有订阅任何来源，在「发现更多来源」里挑选并添加。</p>
      )}
      <button type="button" className="m-wide" onClick={onDiscover}>
        <Compass aria-hidden="true" />
        发现更多来源
      </button>
    </div>
  );
}
