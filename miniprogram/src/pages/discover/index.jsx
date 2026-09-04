import { useEffect, useMemo, useState } from 'react';
import Taro, { useRouter } from '@tarojs/taro';
import { View, Text, Input } from '@tarojs/components';
import { useReaderStore, subscribe, unsubscribe, loadSources } from '../../store/reader';
import { groupByRole, SHAPE_LABELS } from '../../shared/sourceRole';

const SHAPES = ['article', 'podcast', 'bulletin', 'social'];

/**
 * 发现页(DiscoverPage 的小程序翻译,首期只做「源」视图;合集视图 P2):
 * 全站可订阅源目录,形态 seg + 搜索过滤 + 按信息角色分组 + 一键订阅/退订。
 * 从播客容器进入时锁定 shape=podcast(Issue #7 P0.1 专用「添加播客」目录口径)。
 */
export default function DiscoverPage() {
  const { params } = useRouter();
  const store = useReaderStore();
  const locked = params.shape === 'podcast';
  const [shape, setShape] = useState(SHAPES.includes(params.shape) ? params.shape : 'article');
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(null);
  useEffect(() => { loadSources({ force: true }).catch(() => {}); }, []);

  const groups = useMemo(() => {
    const kw = q.trim().toLowerCase();
    const list = store.sources.filter((s) => (s.shape || 'article') === shape && !s.hidden)
      .filter((s) => !kw || String(s.name || '').toLowerCase().includes(kw) || String(s.description || '').toLowerCase().includes(kw));
    return groupByRole(list);
  }, [store.sources, shape, q]);

  const toggle = async (s) => {
    setBusy(s.source_id);
    try {
      if (s.subscribed) { await unsubscribe(s.source_id); Taro.showToast({ title: '已退订', icon: 'none', duration: 800 }); }
      else { await subscribe(s.source_id); Taro.showToast({ title: '已订阅', icon: 'none', duration: 800 }); }
    } catch (err) { Taro.showToast({ title: err.message, icon: 'none' }); }
    finally { setBusy(null); }
  };

  return (
    <View style={{ paddingBottom: 'calc(24px + env(safe-area-inset-bottom))' }}>
      <View className="topbar" style={{ gap: '10px' }}>
        {!locked && (
          <View className="mini-seg">
            {SHAPES.map((s) => (
              <View key={s} className={`mini-seg-btn ${shape === s ? 'is-on' : ''}`} onClick={() => setShape(s)}>{SHAPE_LABELS[s]}</View>
            ))}
          </View>
        )}
        <Input className="search-inline" value={q} placeholder="搜索来源…" onInput={(e) => setQ(e.detail.value)} />
      </View>
      {!store.sourcesLoaded ? <View className="empty">载入中…</View>
        : groups.length === 0 ? <View className="empty"><Text>没有匹配的来源</Text></View>
        : groups.map((g) => (
          <View key={g.key}>
            <View className="group-head">{g.label}</View>
            <View className="card-group" style={{ marginTop: 0 }}>
              {g.list.map((s) => (
                <View key={s.source_id} className="row" style={{ alignItems: 'flex-start' }}>
                  <View style={{ flex: 1, minWidth: 0 }}>
                    <View style={{ fontWeight: 600 }}>{s.name || s.source_id}</View>
                    {s.description && <View style={{ fontSize: '12px', color: 'var(--dorami-muted)', marginTop: '2px', lineHeight: 1.5 }}>{s.description}</View>}
                    <View style={{ fontSize: '11px', color: 'var(--dorami-faint)', marginTop: '4px' }}>
                      {s.count ? `收录 ${s.count} 篇` : ''}{s.subscriber_count ? ` · ${s.subscriber_count} 人订阅` : ''}
                    </View>
                  </View>
                  <View
                    className={`btn is-pill ${s.subscribed ? '' : 'is-primary'} ${busy === s.source_id ? 'is-disabled' : ''}`}
                    onClick={() => busy !== s.source_id && toggle(s)}
                  >
                    {s.subscribed ? '已订阅' : '订阅'}
                  </View>
                </View>
              ))}
            </View>
          </View>
        ))}
    </View>
  );
}
