import { useEffect, useState } from 'react';
import { Check, Copy, Eye, EyeOff, KeyRound, Loader2, RotateCw } from 'lucide-react';
import { API_BASE_URL } from '../../config';
import { fetchFeedToken, rotateFeedToken } from '../../api';
import { copyText } from '../../utils/clipboard';
import { runAction } from '../../utils/runAction';
import { useConfirm } from '../../hooks/useConfirm';

function apiRoot() {
  const base = API_BASE_URL.startsWith('http') ? API_BASE_URL : `${window.location.origin}${API_BASE_URL}`;
  return base.replace(/\/$/, '');
}

function feedEndpoint(suffix = '') {
  return `${apiRoot()}/public/feed/articles${suffix}`;
}

const FEED_PARAMS = [
  ['publish_date_start / _end', '发布时间窗口（YYYY-MM-DD），生成日报最常用'],
  ['content_types', '逗号分隔的内容类型，如 rss_article,web_article'],
  ['source_ids', '逗号分隔的来源；仅取与你已订阅来源的交集', '逗号分隔的来源'],   // 第三项 = 管理员令牌的说法(不受订阅限制)
  ['search', '标题关键词过滤'],
  ['include_content', '是否下发正文，默认 true；传 false 仅取元数据'],
  ['skip / limit', '分页偏移与条数，limit 上限 500'],
];

/**
 * 聚合接口（设置柜·接入集成组）：一个 dfeed_ 令牌拉走当前用户全部已订阅来源，
 * 适合 RSS 工具与脚本。前身是接入集成页签的 FeedAccessSection 卡（并入设置波），
 * 令牌 get/rotate 逻辑原样内聚；MCP 工具调用与这里的 HTTP 拉取共用同一枚令牌。
 */
export default function FeedTokenSection({ showToast, adminScope = false }) {
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

  const curlExample = `curl -H "Authorization: Bearer $DORAMI_TOKEN" \\\n  "${feedEndpoint('?limit=20')}"`;

  return (
    <div>
      <div className="card-head">
        <span className="sett-lbl">个人聚合令牌</span>
        {/* 这枚令牌能取到什么(数据,不是说明):管理员的不受订阅限制。按真实账号角色,不按当前在哪个界面 */}
        <span className="target-chip">{adminScope ? '全站公开内容' : '已订阅来源'}</span>
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

      <div className="endpoint">
        <span className="endpoint-method">GET</span>
        <span className="endpoint-url" title={feedEndpoint()}>{feedEndpoint()}</span>
        <button type="button" className="copybtn" title="复制接口地址" onClick={() => copy(feedEndpoint(), 'feed-json', '聚合接口地址')}>
          {copiedKey === 'feed-json' ? <Check /> : <Copy />}
        </button>
      </div>
      <div className="endpoint">
        <span className="endpoint-method">GET</span>
        <span className="endpoint-url" title={feedEndpoint('.md')}>{feedEndpoint('.md')}</span>
        <button type="button" className="copybtn" title="复制 Markdown 接口地址" onClick={() => copy(feedEndpoint('.md'), 'feed-md', 'Markdown 接口地址')}>
          {copiedKey === 'feed-md' ? <Check /> : <Copy />}
        </button>
      </div>

      <div className="codeblock">
        {curlExample}
        <button type="button" className="copybtn" title="复制 curl 示例" onClick={() => copy(curlExample, 'feed-curl', 'curl 示例')}>
          {copiedKey === 'feed-curl' ? <Check /> : <Copy />}
        </button>
      </div>

      <details className="scope-note">
        <summary>接口参数</summary>
        {FEED_PARAMS.map(([name, desc, adminDesc]) => (
          <p key={name}><code className="font-mono">{name}</code>：{adminScope && adminDesc ? adminDesc : desc}</p>
        ))}
      </details>
    </div>
  );
}
