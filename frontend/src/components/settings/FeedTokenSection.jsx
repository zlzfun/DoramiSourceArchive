import { Fragment, useEffect, useRef, useState } from 'react';
import { Check, ChevronRight, Copy, Eye, EyeOff, KeyRound, Loader2, RotateCw } from 'lucide-react';
import { API_BASE_URL } from '../../config';
import { fetchFeedToken, rotateFeedToken } from '../../api';
import { copyText } from '../../utils/clipboard';
import { runAction } from '../../utils/runAction';
import { useConfirm } from '../../hooks/useConfirm';
import {
  FEED_ENDPOINT_GROUPS,
  feedEndpointExample,
  feedEndpointParams,
  feedEndpointUrl,
  feedScopeLabel,
} from '../../utils/feedEndpoints';

function apiRoot() {
  const base = API_BASE_URL.startsWith('http') ? API_BASE_URL : `${window.location.origin}${API_BASE_URL}`;
  return base.replace(/\/$/, '');
}

/**
 * 聚合接口（设置柜·接入集成组）：一枚 dfeed_ 令牌能调的全部 HTTP 接口，适合 RSS 工具与脚本。
 * 前身是接入集成页签的 FeedAccessSection 卡（并入设置波），令牌 get/rotate 逻辑原样内聚；
 * MCP 工具调用与这里的 HTTP 拉取共用同一枚令牌（卡头有一枚通往「MCP 接入」的链接，那边也有一枚通回来）。
 *
 * 接口清单不写在这里：照 utils/feedEndpoints.js 渲染（issue #315）。按取到的范围分两组，范围签挂在组上——
 * 文章按订阅裁剪、热点事件是全站，一枚令牌两种范围，写在卡头就只对得上其中一组。
 * 一行一个接口；点开一行是它的调用示例和它认的参数，同时只开一行，打开时全收起（五行一屏看全）。
 */
export default function FeedTokenSection({ showToast, adminScope = false, onOpenMcp }) {
  const confirm = useConfirm();
  const [feedToken, setFeedToken] = useState(null);
  const [loading, setLoading] = useState(true);
  // 读取失败 ≠ 未签发:不知道有没有令牌时不给直接签发的入口,只给重试——否则已有令牌的人一点就把旧的换掉了(检视首轮 P2)
  const [loadFailed, setLoadFailed] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [plainToken, setPlainToken] = useState('');   // 明文仅本次生成后可见
  const [revealed, setRevealed] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [copiedKey, setCopiedKey] = useState('');
  const [openKey, setOpenKey] = useState('');   // 点开的那一行(接口 key);'' = 全收起
  const listRef = useRef(null);

  useEffect(() => {
    let mounted = true;
    fetchFeedToken()
      .then(data => { if (mounted) { setFeedToken(data); setLoadFailed(false); } })
      .catch(error => {
        if (mounted) setLoadFailed(true);
        showToast?.(error.message || '获取聚合接口令牌失败', 'error');
      })
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [showToast, reloadKey]);
  const retryLoad = () => { setLoading(true); setReloadKey((key) => key + 1); };

  const copy = (text, key, label) => runAction(() => copyText(text), {
    showToast,
    success: `已复制 ${label}`,
    error: '复制失败，请手动选择文本复制',
    onSuccess: () => { setCopiedKey(key); setTimeout(() => setCopiedKey(''), 1600); },
  });

  const handleRotate = () => runAction(() => rotateFeedToken(), {
    showToast,
    setLoading: setRotating,
    success: '已生成 个人聚合令牌',
    onSuccess: (result) => {
      setPlainToken(result.token);
      setRevealed(true);
      // 新令牌从零算起:旧令牌的使用情况不带过来(随后的重读以服务器为准)
      setFeedToken(prev => ({ ...(prev || {}), exists: true, token_preview: result.token_preview, last_used_at: null, usage_calls: 0 }));
      setLoadFailed(false);
      // 签发日期以服务器记的为准(首次签发才记、轮换不改;服务器存的是它的本地时间,前端自己补会差一天),签发后重读一次状态
      fetchFeedToken().then(data => setFeedToken(data)).catch(() => {});
    },
  });

  const exists = Boolean(feedToken?.exists);
  const displayVal = loading
    ? '读取令牌状态…'
    : revealed && plainToken
      ? plainToken
      : exists
        ? feedToken.token_preview
        : '';
  const issuedAt = feedToken?.created_at ? String(feedToken.created_at).slice(0, 10) : '';
  // 这枚令牌的使用情况(issue #312):最近一次调用 + 近 N 天调用次数。数据,不是说明;没调过就不出。
  // 时间按字符取、不做时区换算(同签发日期:服务器存的是它的本地时间)。
  const lastUsed = feedToken?.last_used_at ? String(feedToken.last_used_at).slice(5, 16).replace('T', ' ') : '';
  const usedCalls = feedToken?.usage_calls || 0;
  const tokenMeta = [
    issuedAt && `签发于 ${issuedAt}`,
    lastUsed && `最近调用 ${lastUsed}`,
    lastUsed && usedCalls > 0 && `近 ${feedToken.usage_window_days} 天 ${usedCalls} 次`,
  ].filter(Boolean).join(' · ');
  // 还没签发:令牌槽的位置只放一枚「签发令牌」。原先是胶囊「未签发」+ 槽里「尚未签发」+ 一句指路,
  // 同一件事写三遍,第一次签发的按钮还叫「重新生成」(issue #291)。
  // 只有成功读到「没有令牌」才算未签发;读取失败是另一种状态(下面的 unknown),给重试。
  const unissued = !loading && !loadFailed && feedToken?.exists === false && !plainToken;
  const unknown = !loading && loadFailed && !plainToken;
  // 已有令牌时再生成会让旧的失效:后果在点下去的那一刻确认,不常驻在令牌下方。
  const requestRotate = async () => {
    if (exists && !(await confirm('重新生成后，旧令牌立即失效。继续？'))) return;
    handleRotate();
  };

  const handleReveal = () => {
    if (!plainToken) {
      showToast?.('完整令牌仅在生成时显示一次，请点右侧「重新生成」获取新令牌', 'info');
      return;
    }
    setRevealed(v => !v);
  };

  const handleCopyToken = () => {
    if (!plainToken) {
      showToast?.('完整令牌仅在生成时显示一次，请点「重新生成」获取新令牌后复制', 'info');
      return;
    }
    copy(plainToken, 'feed-token', '聚合接口令牌');
  };

  // 开合以 <details> 自己的状态为准(点行、键盘、浏览器页内查找都会改它),这里只跟着记:开了一行就把别的收起。
  const handleToggle = (event, key) => {
    const isOpen = event.currentTarget.open;
    setOpenKey((prev) => (isOpen ? key : prev === key ? '' : prev));
  };
  // 点开靠下的一行时,它的正文可能落在可视区外:等别的行收起、版面定下来之后,把这一行带进来(不做滚动动画)。
  useEffect(() => {
    if (openKey) listRef.current?.querySelector('.ep[open]')?.scrollIntoView({ block: 'nearest' });
  }, [openKey]);
  // 行尾的复制钮在 <summary> 里:点它只复制,不开合这一行。
  const copyInRow = (event, text, key, label) => {
    event.preventDefault();
    event.stopPropagation();
    copy(text, key, label);
  };

  const root = apiRoot();

  return (
    <div ref={listRef}>
      <div className="card-head">
        <span className="sett-lbl">个人聚合令牌</span>
        {onOpenMcp && (
          <button type="button" className="sett-link ml-auto inline-flex items-center gap-0.5 text-xs" onClick={onOpenMcp}>
            MCP 接入 <ChevronRight className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {unissued ? (
        <button type="button" className="action-button action-button-primary min-h-[32px] px-3 text-xs" onClick={handleRotate} disabled={rotating}>
          {rotating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />} 签发令牌
        </button>
      ) : unknown ? (
        <div className="token-row">
          <span className="token-val">读取失败</span>
          <button type="button" className="action-button action-button-secondary min-h-[32px] px-3 text-xs" onClick={retryLoad}>重试</button>
        </div>
      ) : (
      <div className="token-row">
        <span className="token-val" title={revealed && plainToken ? plainToken : undefined}>{displayVal}</span>
        <button type="button" className="copybtn" title={revealed ? '隐藏' : '显示'} onClick={handleReveal} disabled={!exists && !plainToken}>
          {revealed && plainToken ? <EyeOff /> : <Eye />}
        </button>
        <button type="button" className="copybtn" title="复制令牌" onClick={handleCopyToken} disabled={!exists && !plainToken}>
          {copiedKey === 'feed-token' ? <Check /> : <Copy />}
        </button>
        <button type="button" className="copybtn" title="重新生成（旧令牌立即失效）" onClick={requestRotate} disabled={rotating || loading}>
          {rotating ? <Loader2 className="animate-spin" /> : <RotateCw />}
        </button>
      </div>
      )}
      {exists && tokenMeta && <div className="token-meta">{tokenMeta}</div>}

      {FEED_ENDPOINT_GROUPS.map((group) => (
        <Fragment key={group.key}>
          <div className="ep-group">
            <span className="sett-lbl">{group.label}</span>
            {/* 这一组取到什么(数据,不是说明):管理员的文章不受订阅限制。按真实账号角色,不按当前在哪个界面 */}
            <span className="target-chip">{feedScopeLabel(group, adminScope)}</span>
          </div>
          {group.endpoints.map((endpoint) => {
            const url = feedEndpointUrl(root, endpoint);
            const example = feedEndpointExample(root, endpoint);
            const urlKey = `url:${endpoint.key}`;
            const exampleKey = `example:${endpoint.key}`;
            return (
              <details key={endpoint.key} className="ep" open={openKey === endpoint.key} onToggle={(event) => handleToggle(event, endpoint.key)}>
                <summary className="endpoint">
                  <span className="endpoint-method">{endpoint.method}</span>
                  <span className="endpoint-url" title={url}>{url}</span>
                  <span className="endpoint-fmt">{endpoint.format}</span>
                  <button type="button" className="copybtn" title="复制接口地址" onClick={(event) => copyInRow(event, url, urlKey, '接口地址')}>
                    {copiedKey === urlKey ? <Check /> : <Copy />}
                  </button>
                </summary>
                <div className="ep-body">
                  <div className="ep-code">
                    {example}
                    <button type="button" className="copybtn" title="复制示例" onClick={() => copy(example, exampleKey, '调用示例')}>
                      {copiedKey === exampleKey ? <Check /> : <Copy />}
                    </button>
                  </div>
                  <div className="ep-params">
                    {feedEndpointParams(endpoint, adminScope).map(([name, desc]) => (
                      <p key={name}><code className="font-mono">{name}</code>：{desc}</p>
                    ))}
                  </div>
                </div>
              </details>
            );
          })}
        </Fragment>
      ))}
    </div>
  );
}
