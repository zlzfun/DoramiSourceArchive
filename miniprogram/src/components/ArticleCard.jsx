import { View, Text } from '@tarojs/components';
import { excerptOf, formatPodcastDuration } from '../shared/readerText';
import { formatRelativeTime, dayLabelOf } from '../shared/readerTime';
import ProxyImage from './ProxyImage';

function scoreText(score) {
  if (score === null || score === undefined || score === '') return '';
  const n = Number(score);
  return Number.isFinite(n) ? n.toFixed(1) : '';
}

/** 条目卡(ReaderTab.ArticleRow 的小程序翻译):来源行 / 未读点 / 标题 / 收藏星 / 摘要 / 分析签 */
export default function ArticleCard({ article, shape, sourceName, isUnread, isFav, showLabel, dayKey, onOpen, onToggleFavorite }) {
  const isBulletin = shape === 'bulletin';
  const isPodcast = shape === 'podcast' || article.content_type === 'podcast_episode';
  const podcast = isPodcast ? (article.podcast || {}) : null;
  const excerpt = isBulletin ? '' : excerptOf(article.one_sentence_summary || article.summary_zh || article.content_preview || '');
  const score = scoreText(article.quality_score);
  const time = formatRelativeTime(article.publish_date || article.fetched_date, '');
  const star = (
    <Text className={`fav-star ${isFav ? 'is-on' : ''}`} onClick={(e) => { e.stopPropagation(); onToggleFavorite(article); }}>
      {isFav ? '★' : '☆'}
    </Text>
  );
  return (
    <>
      {showLabel && <View className="date-label">{dayLabelOf(dayKey)}</View>}
      <View className={`entry ${isBulletin ? 'is-bulletin' : ''} ${isPodcast ? 'is-podcast' : ''}`} onClick={() => onOpen(article)}>
        {isUnread && <View className="unread-dot" />}
        {isPodcast ? (
          <View className="podcast-layout">
            {podcast.image_url ? (
              <ProxyImage className="entry-cover" src={podcast.image_url} />
            ) : <View className="entry-cover" />}
            <View className="podcast-copy">
              <View className="entry-top">
                <Text className="entry-src">{podcast.show_title || sourceName}</Text>
                <Text className="entry-time">{time}</Text>
              </View>
              <View className="entry-titlerow">
                <Text className="entry-title">{article.title || '（无标题）'}</Text>
                {star}
              </View>
              <View className="entry-meta">
                {formatPodcastDuration(podcast.duration_seconds) && <Text>{formatPodcastDuration(podcast.duration_seconds)}</Text>}
              </View>
            </View>
          </View>
        ) : (
          <>
            <View className="entry-top">
              <Text className="entry-src">{sourceName}</Text>
              <Text className="entry-time">{time}</Text>
            </View>
            <View className="entry-titlerow">
              <Text className="entry-title">{article.title || '（无标题）'}</Text>
              {star}
            </View>
            {excerpt ? <View className="entry-excerpt">{excerpt}</View> : null}
            {(score || article.primary_tag) && (
              <View className="entry-meta">
                {score && <Text className="score-chip">{score}</Text>}
                {article.primary_tag && <Text>{article.primary_tag.name || article.primary_tag}</Text>}
              </View>
            )}
          </>
        )}
      </View>
    </>
  );
}
