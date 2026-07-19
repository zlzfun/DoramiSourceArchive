import { useEffect, useState } from 'react';
import { Check, Copy, Eye, EyeOff, Loader2, RotateCw } from 'lucide-react';
import { API_BASE_URL } from '../../config';
import { fetchFeedToken, rotateFeedToken } from '../../api';
import { copyText } from '../../utils/clipboard';
import { runAction } from '../../utils/runAction';

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
  ['source_ids', '逗号分隔的来源；仅取与你已订阅来源的交集'],
  ['search', '标题关键词过滤'],
  ['include_content', '是否下发正文，默认 true；传 false 仅取元数据'],
  ['skip / limit', '分页偏移与条数，limit 上限 500'],
];

/**
 * 聚合接口（设置柜·接入集成组）：一个 dfeed_ 令牌拉走当前用户全部已订阅来源，
 * 适合 RSS 工具与脚本。前身是接入集成页签的 FeedAccessSection 卡（并入设置波），
 * 令牌 get/rotate 逻辑原样内聚；MCP 工具调用与这里的 HTTP 拉取共用同一枚令牌。
 */
export default function FeedTokenSection({ showToast, isAdmin = false }) {
  const [feedToken, setFeedToken] = useState(null);
  const [loading, setLoading] = useState(true);
  const [plainToken, setPlainToken] = useState('');   // 明文仅本次生成后可见
  const [revealed, setRevealed] = useState(false);
  const [rotating, setRotating] = useState(false);
  const [copiedKey, setCopiedKey] = useState('');

  useEffect(() => {
    let mounted = true;
    fetchFeedToken()
      .then(data => { if (mounted) setFeedToken(data); })
      .catch(error => showToast?.(error.message || '获取聚合接口令牌失败', 'error'))
      .finally(() => { if (mounted) setLoading(false); });
    return () => { mounted = false; };
  }, [showToast]);

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
      setFeedToken(prev => ({ ...(prev || {}), exists: true, token_preview: result.token_preview }));
    },
  });

  const exists = Boolean(feedToken?.exists);
  const displayVal = loading
    ? '读取令牌状态…'
    : revealed && plainToken
      ? plainToken
      : exists
        ? feedToken.token_preview
        : '尚未签发';
  const issuedAt = feedToken?.created_at ? String(feedToken.created_at).slice(0, 10) : '';

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
        <span className={`stamp ${exists ? 'stamp-ok' : 'stamp-idle'}`}>{exists ? '已签发' : '未签发'}</span>
      </div>
      <p className="card-desc">{isAdmin
        ? '管理员令牌不受订阅限制，一个令牌拉走全库内容，适合 RSS 工具与脚本；MCP 工具调用也共用它。'
        : '一个令牌拉走你订阅的全部来源，适合 RSS 工具与脚本；MCP 工具调用也共用它。'}</p>

      <div className="token-row">
        <span className="token-val" title={revealed && plainToken ? plainToken : undefined}>{displayVal}</span>
        <button type="button" className="copybtn" title={revealed ? '隐藏' : '显示'} onClick={handleReveal} disabled={!exists && !plainToken}>
          {revealed && plainToken ? <EyeOff /> : <Eye />}
        </button>
        <button type="button" className="copybtn" title="复制令牌" onClick={handleCopyToken} disabled={!exists && !plainToken}>
          {copiedKey === 'feed-token' ? <Check /> : <Copy />}
        </button>
        <button type="button" className="copybtn" title="重新生成（旧令牌立即失效）" onClick={handleRotate} disabled={rotating}>
          {rotating ? <Loader2 className="animate-spin" /> : <RotateCw />}
        </button>
      </div>
      <div className="token-meta">
        {exists
          ? `${issuedAt ? `签发于 ${issuedAt}，` : ''}重新生成会让旧令牌立即失效`
          : '尚未签发，点上方「重新生成」创建你的第一个令牌'}
      </div>

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
      <p className="tiny-meta mt-2">把 <code className="font-mono">$DORAMI_TOKEN</code> 换成上方 dfeed_ 令牌；{isAdmin ? '管理员令牌不限订阅，返回全库内容。' : '只返回你已订阅来源的内容。'}</p>

      <details className="scope-note">
        <summary>接口参数</summary>
        {FEED_PARAMS.map(([name, desc]) => (
          <p key={name}><code className="font-mono">{name}</code>：{desc}</p>
        ))}
      </details>
    </div>
  );
}
