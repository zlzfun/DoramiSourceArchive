/**
 * 账户抽屉「接口调用」区(issue #312):这个账号的令牌,以及每枚令牌调过哪些接口、调了多少。
 * 一枚令牌一张卡——卡头是类型 + 尾号(作废的挂「已作废」)与调用次数 / 返回条数,展开是它调过的每个接口;
 * 其下是每日接口调用图。账号没有可列的令牌时整区不渲染(由调用方按 tokens.length 判断)。
 * 样页 docs/design/issue-312-token-usage/(方向 A)。
 */
import { useMemo, useState } from 'react';
import { MultiSeriesBars } from '../charts/DashboardCharts';
import { formatStamp } from './adminUtils';
import {
  channelLabel,
  deliveryGroupColor,
  deliverySectionTitle,
  pivotDeliveryDaily,
  tokenKindLabel,
  tokenMetaLine,
} from '../../utils/deliveryChannels';

const CHART_DIMS = [['calls', '调用'], ['items', '条数']];

export default function AccountDelivery({ delivery }) {
  const tokens = useMemo(() => delivery?.tokens ?? [], [delivery]);
  // 默认展开第一枚(现用且调得最多的那枚);之后各卡的开合由读者自己定。
  const [openMap, setOpenMap] = useState({});
  const isOpen = (token, index) => (
    token.fingerprint in openMap ? openMap[token.fingerprint] : index === 0
  );
  const windowDays = delivery?.window_days ?? 30;
  const datasets = useMemo(() => ({
    calls: pivotDeliveryDaily(delivery?.by_day, windowDays, 'calls'),
    items: pivotDeliveryDaily(delivery?.by_day, windowDays, 'items'),
  }), [delivery, windowDays]);

  if (!tokens.length) return null;
  return (
    <>
      <div>
        <div className="drawer-sec-title">{deliverySectionTitle(delivery)}</div>
        <div className="tok-list">
          {tokens.map((token, index) => {
            const channels = token.channels ?? [];
            const peak = Math.max(1, ...channels.map((c) => c.calls || 0));
            return (
              <details
                key={token.fingerprint}
                className="tok-card"
                open={channels.length > 0 && isOpen(token, index)}
                onToggle={(e) => {
                  const open = e.currentTarget.open;
                  setOpenMap((prev) => (prev[token.fingerprint] === open ? prev : { ...prev, [token.fingerprint]: open }));
                }}
              >
                <summary>
                  <span className="tok-id">
                    <span className="tok-kind">{tokenKindLabel(token)}</span>
                    <span className="tok-prev">{token.preview}</span>
                    {token.status === 'retired' && <span className="stamp stamp-idle">已作废</span>}
                  </span>
                  <span className={`tok-n ${token.calls ? '' : 'is-zero'}`}>{token.calls || '–'}</span>
                  <span className="tok-meta">{tokenMetaLine(token, formatStamp)}</span>
                  <span className="tok-items">{token.items ? `${token.items} 条` : ''}</span>
                </summary>
                {channels.length > 0 && (
                  <div className="tok-chs">
                    {channels.map((c) => (
                      <div key={c.channel} className="rounded-[var(--r-sm)] px-1.5 py-1.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="tok-ch-name" title={channelLabel(c.channel)}>{channelLabel(c.channel)}</span>
                          <span className="shrink-0 tiny-meta tabular-nums text-slate-500">{c.calls} 次 · {c.items} 条</span>
                        </div>
                        <div className="mt-1">
                          <div className="h-1.5 overflow-hidden rounded-r-full bg-[var(--dorami-well)]">
                            <div className="h-full rounded-r-full" style={{ width: `${((c.calls || 0) / peak) * 100}%`, background: deliveryGroupColor(c.group) }} />
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </details>
            );
          })}
        </div>
      </div>
      {(delivery?.totals?.calls ?? 0) > 0 && (
        <MultiSeriesBars
          title="每日接口调用"
          datasets={datasets}
          dims={CHART_DIMS}
          height={200}
          namespace="user-delivery"
          colorFor={deliveryGroupColor}
          alwaysLegend
        />
      )}
    </>
  );
}
