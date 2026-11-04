import { ArticleRow } from '../ReaderTab';

const noop = () => {};

// 长按预览卡(issue #230 批 4a;样页拍板 5「长按写清对象 = 抬起预览卡」、拍板 22「长按扩到播客卡」):
// 被按的那一条原样再画一遍(同一个 ArticleRow 夜航卡,文章 / 播客两种版式;批 4c 起与列表卡同一套排印),放在动作单上方的卡面里;
// 外层 .m-sheet-peek 是 aria-hidden + inert,这里的按钮不进 Tab、不接指针,读屏由动作单名说清对象。
export default function EntryPeek({ article, podcast = false, source = null, sourceName = '', isUnread = false, isFav = false }) {
  return (
    <div className="m-peek-card">
      <ArticleRow
        article={article}
        active={false}
        isUnread={isUnread}
        isFav={isFav}
        entryPodcast={podcast}
        showLabel={false}
        dayKey=""
        searchQuery=""
        source={source}
        sourceName={sourceName}
        onSelect={noop}
        onPrefetchEnter={noop}
        onPrefetchLeave={noop}
        onToggleFavorite={noop}
        onContextMenu={noop}
        ctxAnchor={false}
        favIndicator
        card
      />
    </div>
  );
}
