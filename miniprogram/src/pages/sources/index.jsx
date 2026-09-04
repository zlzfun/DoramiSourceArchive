import Taro, { useRouter } from '@tarojs/taro';
import { View, Text } from '@tarojs/components';
import { useReaderStore, setFilter, subscribedSources, unsubscribe, loadSources } from '../../store/reader';
import { groupByRole, SHAPE_ALL_LABEL } from '../../shared/sourceRole';
import ProxyImage from '../../components/ProxyImage';

const HUES = [210, 260, 20, 150, 330, 45, 180, 290];
function hueOf(str) { let h = 0; for (const ch of String(str || '')) h = (h * 31 + ch.charCodeAt(0)) >>> 0; return HUES[h % HUES.length]; }

/**
 * 源过滤页(MobileSourceDrawer 的页面翻译):聚合入口(全部XX / 只看收藏)+ 按信息角色分组的已订阅源;
 * 选中即写过滤器并返回条目流(§4.1.2 选源是过滤器不是目的地)。长按源行 → 退订确认。
 */
export default function SourcesPage() {
  const { params } = useRouter();
  const shape = params.shape || 'article';
  const store = useReaderStore();
  const filter = store.filters[shape];
  const list = subscribedSources().filter((s) => s.shape === shape);
  const groups = groupByRole(list);
  const pick = (patch) => { setFilter(shape, { ...patch, search: '' }); Taro.navigateBack(); };
  const onLongPress = (source) => {
    Taro.showActionSheet({ itemList: ['退订该来源'], itemColor: '#dc2626' }).then(async () => {
      try { await unsubscribe(source.source_id); Taro.showToast({ title: '已退订', icon: 'none' }); }
      catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
    }).catch(() => {});
  };
  return (
    <View style={{ paddingBottom: 'calc(24px + env(safe-area-inset-bottom))' }}>
      <View className="card-group">
        <View className={`row ${!filter.sourceId && !filter.favOnly ? 'is-on' : ''}`} onClick={() => pick({ sourceId: null, favOnly: false })}>
          <Text className="row-label">{SHAPE_ALL_LABEL[shape]}</Text>
          {store.unreadTotal > 0 && !filter.favOnly && <Text className="row-meta">{list.reduce((n, s) => n + (store.unreadBySource[s.source_id] || 0), 0) || ''}</Text>}
        </View>
        <View className={`row is-fav ${filter.favOnly ? 'is-on' : ''}`} onClick={() => pick({ sourceId: null, favOnly: true })}>
          <Text>{filter.favOnly ? '★' : '☆'}</Text>
          <Text className="row-label">只看收藏</Text>
        </View>
      </View>
      {store.sourcesLoading && !store.sourcesLoaded ? (
        <View className="empty">载入中…</View>
      ) : groups.length === 0 ? (
        <View className="empty"><Text>还没有订阅任何来源</Text></View>
      ) : groups.map((g) => (
        <View key={g.key}>
          <View className="group-head">{g.label}</View>
          <View className="card-group" style={{ marginTop: 0 }}>
            {g.list.map((s) => {
              const unread = store.unreadBySource[s.source_id] || 0;
              const on = filter.sourceId === s.source_id;
              return (
                <View
                  key={s.source_id}
                  className={`row ${on ? 'is-on' : ''} ${s.hidden ? 'is-unavailable' : ''}`}
                  onClick={() => (s.hidden ? Taro.showToast({ title: '该来源暂不可用', icon: 'none' }) : pick({ sourceId: s.source_id, favOnly: false }))}
                  onLongPress={() => onLongPress(s)}
                >
                  {s.avatar_url ? (
                    <ProxyImage className="row-avatar" src={s.avatar_url} />
                  ) : (
                    <View className="row-avatar row-avatar-fallback" style={{ background: `hsl(${hueOf(s.source_id)} 45% 52%)` }}>{s.icon || String(s.name || s.source_id).slice(0, 1).toUpperCase()}</View>
                  )}
                  <Text className="row-label" style={unread > 0 ? { fontWeight: 700 } : undefined}>{s.name || s.source_id}</Text>
                  {s.hidden ? <Text className="badge">暂不可用</Text> : unread > 0 ? <Text className="row-meta">{unread}</Text> : null}
                </View>
              );
            })}
          </View>
        </View>
      ))}
      <View style={{ padding: '18px 12px 0' }}>
        <View className="btn is-block" onClick={() => Taro.navigateTo({ url: `/pages/discover/index?shape=${shape}` })}>
          {shape === 'podcast' ? '添加播客' : '发现更多来源'}
        </View>
        <View className="toast-inline" onClick={() => loadSources({ force: true })}>长按来源可退订 · 点此刷新目录</View>
      </View>
    </View>
  );
}
