import { useEffect, useMemo, useRef, useState } from 'react';
import Taro, { useRouter, useShareAppMessage, useUnload } from '@tarojs/taro';
import { View, Text, RichText, Image, Button, Slider } from '@tarojs/components';
import { renderArticle, recordArticleRead, markArticleUnread } from '../../api';
import { useReaderStore, toggleFavorite, sourceName, overrideRead, applyUnreadCounts } from '../../store/reader';
import { formatDateTime } from '../../shared/readerTime';
import { hostOf, formatPodcastDuration } from '../../shared/readerText';
import { absolutizeMediaPath } from '../../config';
import './index.scss';

// 服务端 HTML 里的签名图链是站内相对路径 → 拼 origin;图片限宽由内联 style 承担(rich-text 不吃外部 img 选择器)
function prepareHtml(html) {
  return String(html || '')
    .replace(/src="\/api\/public\/media/g, `src="${absolutizeMediaPath('/api/public/media')}`)
    .replace(/<img /g, '<img style="max-width:100%;height:auto;border-radius:6px;display:block;margin:10px auto" ')
    .replace(/<pre>/g, '<pre style="overflow-x:auto;padding:10px 12px;border-radius:8px;background:rgba(11,18,32,0.06);font-size:13px;line-height:1.6">')
    .replace(/<blockquote>/g, '<blockquote style="margin:10px 0;padding:2px 12px;border-left:3px solid rgba(91,84,232,0.45);color:inherit;opacity:0.85">')
    .replace(/<table>/g, '<table style="border-collapse:collapse;width:100%;font-size:13px">')
    .replace(/<(th|td)/g, '<$1 style="border:1px solid rgba(11,18,32,0.12);padding:6px 8px"');
}
function fmtClock(sec) {
  if (!Number.isFinite(sec) || sec < 0) return '0:00';
  const m = Math.floor(sec / 60); const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/**
 * 正文页:GET /api/reader/articles/{id}/render → rich-text;顶部 kicker(来源·时间·阅读量)+ 衬线标题
 * + 「原文|中文」二段(has_translation 时)+ 播客播放器;底部工具条 收藏/标未读/分享;进入即计一次阅读。
 * 转发卡片 path 带 id,是站内深链 #/reader/a/{id} 的小程序翻译。
 */
export default function ArticlePage() {
  const { params } = useRouter();
  const id = decodeURIComponent(params.id || '');
  const store = useReaderStore();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [showZh, setShowZh] = useState(false);
  const [readCount, setReadCount] = useState(null);
  // 播客
  const audioRef = useRef(null);
  const [playing, setPlaying] = useState(false);
  const [pos, setPos] = useState(0);
  const [dur, setDur] = useState(0);

  useEffect(() => {
    if (!id) return;
    let alive = true;
    renderArticle(id).then((res) => {
      if (!alive) return;
      setData(res);
      setShowZh(Boolean(res.has_translation && !res.is_chinese));
      Taro.setNavigationBarTitle({ title: sourceName(res.source_id) || '正文' }).catch(() => {});
    }).catch((err) => alive && setError(err.message || '获取正文失败'));
    recordArticleRead(id).then((r) => { if (alive && r && typeof r.read_count === 'number') setReadCount(r.read_count); });
    overrideRead(id, true);
    return () => { alive = false; };
  }, [id]);

  const podcast = data && data.podcast;
  useEffect(() => {
    if (!podcast || !podcast.audio_url) return undefined;
    const ctx = Taro.createInnerAudioContext();
    ctx.src = podcast.audio_url;
    ctx.onPlay(() => setPlaying(true));
    ctx.onPause(() => setPlaying(false));
    ctx.onStop(() => setPlaying(false));
    ctx.onEnded(() => setPlaying(false));
    ctx.onTimeUpdate(() => { setPos(ctx.currentTime); if (ctx.duration) setDur(ctx.duration); });
    ctx.onError((e) => { setPlaying(false); Taro.showToast({ title: `音频无法播放(${e && e.errCode})`, icon: 'none' }); });
    audioRef.current = ctx;
    return () => { try { ctx.destroy(); } catch { /* noop */ } audioRef.current = null; };
  }, [podcast && podcast.audio_url]);
  useUnload(() => { if (audioRef.current) { try { audioRef.current.destroy(); } catch { /* noop */ } } });

  useShareAppMessage(() => ({
    title: (data && (showZh && data.translated_title ? data.translated_title : data.title)) || '哆啦美',
    path: `/pages/article/index?id=${encodeURIComponent(id)}`,
    imageUrl: data && data.cover_image ? absolutizeMediaPath(data.cover_image) : undefined,
  }));

  const html = useMemo(() => prepareHtml(showZh && data && data.translated_html ? data.translated_html : (data && data.html)), [data, showZh]);
  const isFav = store.favoriteIds.has(id);

  const onFav = async () => {
    try { const on = await toggleFavorite(id); Taro.showToast({ title: on ? '已收藏' : '已取消收藏', icon: 'none', duration: 900 }); }
    catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
  };
  const onUnread = async () => {
    try { const r = await markArticleUnread(id); overrideRead(id, false); if (r && r.by_source) applyUnreadCounts(r); Taro.showToast({ title: '已标为未读', icon: 'none', duration: 900 }); }
    catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
  };
  const copyOriginal = () => {
    if (!data || !data.source_url) return;
    Taro.setClipboardData({ data: data.source_url }).then(() => Taro.showToast({ title: '原文链接已复制', icon: 'none' }));
  };
  const togglePlay = () => {
    const ctx = audioRef.current; if (!ctx) return;
    if (playing) ctx.pause(); else ctx.play();
  };

  if (error) {
    return <View className="pane"><View className="empty"><Text>{error}</Text></View></View>;
  }
  if (!data) {
    return (
      <View className="pane skel-body">
        <View className="skeleton" style={{ width: '40%', height: '10px' }} />
        <View className="skeleton" style={{ width: '90%', height: '22px', marginTop: '14px' }} />
        <View className="skeleton" style={{ width: '70%', height: '22px' }} />
        {[92, 78, 85, 96, 60].map((w) => <View key={w} className="skeleton" style={{ width: `${w}%` }} />)}
      </View>
    );
  }
  const title = showZh && data.translated_title ? data.translated_title : (data.title || '（无标题）');
  const showOrig = showZh && data.translated_title && data.translated_title !== data.title;
  return (
    <View className="pane">
      <View className="kicker">
        <Text className="kicker-src">{sourceName(data.source_id)}</Text>
        <Text>·</Text>
        <Text>{formatDateTime(data.publish_date || data.fetched_date)}</Text>
        {readCount !== null && <Text>· 阅读量 {readCount}</Text>}
      </View>
      <View className="pane-title" userSelect>{title}</View>
      {showOrig && <View className="pane-title-orig">{data.title}</View>}
      <View className="pane-actions">
        {data.source_url && <View className="btn is-pill" onClick={copyOriginal}>复制原文链接</View>}
        {data.has_translation && !data.is_chinese && (
          <View className="tr-seg">
            <View className={`tr-seg-btn ${!showZh ? 'is-on' : ''}`} onClick={() => setShowZh(false)}>原文</View>
            <View className={`tr-seg-btn ${showZh ? 'is-ai' : ''}`} onClick={() => setShowZh(true)}>中文</View>
          </View>
        )}
      </View>
      {podcast && podcast.audio_url && (
        <View className="podcast-panel">
          <View className="podcast-row">
            {data.cover_image ? <Image className="podcast-cover" src={absolutizeMediaPath(data.cover_image)} mode="aspectFill" /> : <View className="podcast-cover" />}
            <View style={{ flex: 1, minWidth: 0 }}>
              <View style={{ fontSize: '13px', fontWeight: 600 }}>{podcast.show_title || '原版音频'}</View>
              <View style={{ fontSize: '12px', color: 'var(--dorami-faint)', marginTop: '2px' }}>{formatPodcastDuration(podcast.duration_seconds) || ''}</View>
            </View>
            <View className="podcast-play" onClick={togglePlay}>{playing ? '❚❚' : '▶'}</View>
          </View>
          <View className="podcast-progress">
            <Text>{fmtClock(pos)}</Text>
            <Slider min={0} max={Math.max(1, Math.floor(dur || podcast.duration_seconds || 0))} value={Math.floor(pos)} blockSize={12} activeColor="#5b54e8" onChange={(e) => { const ctx = audioRef.current; if (ctx) ctx.seek(e.detail.value); }} />
            <Text>{fmtClock(dur || podcast.duration_seconds || 0)}</Text>
          </View>
        </View>
      )}
      <View className="body">
        {html ? <RichText className="md" nodes={html} userSelect /> : <View className="empty">暂无正文</View>}
      </View>
      {data.source_url && (
        <View className="tail" onClick={copyOriginal}>
          <Text className="tail-link">查看原文 ↗</Text>
          <Text>· {hostOf(data.source_url)}</Text>
        </View>
      )}
      <View style={{ height: '64px' }} />
      <View className="pane-toolbar">
        <View className={`pane-toolbar-btn ${isFav ? 'is-amber' : ''}`} onClick={onFav}><Text className="ic">{isFav ? '★' : '☆'}</Text><Text>{isFav ? '已收藏' : '收藏'}</Text></View>
        <View className="pane-toolbar-btn" onClick={onUnread}><Text className="ic">●</Text><Text>标为未读</Text></View>
        <View className="pane-toolbar-btn"><Button openType="share"><Text className="ic">↗</Text><Text>分享</Text></Button></View>
      </View>
    </View>
  );
}
