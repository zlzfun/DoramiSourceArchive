import Taro from '@tarojs/taro';
import { View, Text } from '@tarojs/components';
import { formatRelativeTime } from '../shared/readerTime';
import ProxyImage from './ProxyImage';

const HUES = [210, 260, 20, 150, 330, 45, 180, 290];
function hueOf(str) { let h = 0; for (const ch of String(str || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return HUES[h % HUES.length]; }

/**
 * 社交推文卡(SocialFlow 卡片的小程序翻译):转推归属行 / 作者 / 正文(reposted.text 优先)/ 引用推 / 图片网格。
 * 不展示互动数字(快照会过时,v3.12 契约);时间戳即原推链接 → 复制到剪贴板(小程序不能跳外链)。
 * 头像/图片走 media_images(签名链,来自 render 端点)或 proxy 直连(需会话头,Image 组件带不了 → 回退原链)。
 */
export default function SocialCard({ article, sourceName, isUnread, isFav, onToggleFavorite, onToggleRead }) {
  const ext = article.extensions || {};
  const repost = ext.reposted || null;
  const quote = ext.quoted || null;
  const authorName = repost ? (repost.author_name || '') : (ext.author_name || sourceName);
  const authorHandle = repost ? repost.author_handle : ext.author_handle;
  const text = repost ? (repost.text || '') : (article.content || article.content_preview || '');
  const media = (repost && repost.media_urls && repost.media_urls.length ? repost.media_urls : ext.media_urls) || [];
  const avatar = repost ? '' : (ext.author_avatar_url || '');
  const time = formatRelativeTime(article.publish_date || article.fetched_date, '');
  const copyLink = () => {
    const url = article.source_url;
    if (!url) return;
    Taro.setClipboardData({ data: url }).then(() => Taro.showToast({ title: '原推链接已复制', icon: 'none' }));
  };
  return (
    <View className="social-card">
      {repost && <View className="social-repost">↻ {ext.author_name || sourceName} 转推</View>}
      <View className="social-head">
        {avatar ? (
          <ProxyImage className="social-avatar" src={avatar} />
        ) : (
          <View className="social-avatar social-avatar-fallback" style={{ background: `hsl(${hueOf(authorHandle || authorName)} 55% 50%)` }}>
            {String(authorName || '?').slice(0, 1).toUpperCase()}
          </View>
        )}
        <View>
          <View className="social-name">{authorName}</View>
          {authorHandle && <View className="social-handle">@{authorHandle}</View>}
        </View>
        <Text className="social-time" onClick={copyLink}>{time}</Text>
      </View>
      <View className="social-text"><Text userSelect>{text}</Text></View>
      {quote && (
        <View className="social-quote">
          <View className="social-quote-author">{quote.author_name}{quote.author_handle ? ` @${quote.author_handle}` : ''}</View>
          <View className="social-quote-text">{quote.text}</View>
        </View>
      )}
      {media.length > 0 && (
        <View className={`social-media ${media.length === 1 ? 'is-single' : ''}`}>
          {media.slice(0, 4).map((url) => (
            <ProxyImage key={url} src={url} onClick={() => Taro.previewImage({ urls: media, current: url })} />
          ))}
        </View>
      )}
      <View className="social-actions">
        <Text className={isFav ? 'is-on' : ''} onClick={() => onToggleFavorite(article)}>{isFav ? '★ 已收藏' : '☆ 收藏'}</Text>
        <Text onClick={() => onToggleRead(article)}>{isUnread ? '○ 标为已读' : '● 标为未读'}</Text>
      </View>
    </View>
  );
}
