import { useEffect, useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, KeyRound, Loader2, RotateCw, Terminal } from 'lucide-react';
import { API_BASE_URL } from '../config';
import { fetchFeedToken, rotateFeedToken } from '../api';
import { copyText } from '../utils/clipboard';

const TOKEN_PLACEHOLDER = '$DORAMI_TOKEN';

function apiRoot() {
  const base = API_BASE_URL.startsWith('http') ? API_BASE_URL : `${window.location.origin}${API_BASE_URL}`;
  return base.replace(/\/$/, '');
}

function feedEndpoint(suffix = '') {
  return `${apiRoot()}/public/feed/articles${suffix}`;
}

const FEED_PARAMS = [
  ['publish_date_start / publish_date_end', '发布时间窗口（YYYY-MM-DD），生成日报最常用'],
  ['content_types', '逗号分隔的内容类型，如 rss_article,web_article'],
  ['source_ids', '逗号分隔的来源；仅取与你已订阅来源的交集'],
  ['search', '标题关键词过滤'],
  ['include_content', '是否下发正文，默认 true；传 false 仅取元数据'],
  ['has_content', '仅返回有正文的记录，默认 true'],
  ['skip / limit', '分页偏移与条数，limit 上限 500'],
];

function FeedDocsPanel({ plainToken, onCopy, copiedKey }) {
  const token = plainToken || TOKEN_PLACEHOLDER;
  const examples = [
    ['拉取最新（默认 100 条）', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint()}"`],
    ['按发布时间筛选（日报）', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint('?publish_date_start=2026-05-20&publish_date_end=2026-05-26')}"`],
    ['指定类型 + 仅元数据', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint('?content_types=rss_article&include_content=false')}"`],
    ['Markdown 批量导出', `curl -H "Authorization: Bearer ${token}" \\\n  "${apiRoot()}/public/feed/articles.md"`],
  ];
  return (
    <div className="mt-4 space-y-4 border-t border-slate-100 pt-4">
      {!plainToken && (
        <p className="tiny-meta">下例中的 <code className="font-mono">{TOKEN_PLACEHOLDER}</code> 请替换为你的令牌（生成时仅显示一次）。</p>
      )}
      <div>
        <p className="form-label mb-2">请求参数</p>
        <div className="overflow-hidden rounded-[10px] border border-slate-100">
          <table className="w-full text-left text-xs">
            <tbody className="divide-y divide-slate-100">
              {FEED_PARAMS.map(([name, desc]) => (
                <tr key={name} className="align-top">
                  <td className="w-[220px] bg-slate-50 px-3 py-2 font-mono font-bold text-slate-600">{name}</td>
                  <td className="px-3 py-2 text-slate-500">{desc}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="space-y-3">
        <p className="form-label">调用示例（curl）</p>
        {examples.map(([label, cmd], idx) => (
          <div key={label}>
            <div className="mb-1 flex items-center justify-between">
              <span className="tiny-meta">{label}</span>
              <button
                type="button"
                onClick={() => onCopy(cmd, `curl-${idx}`)}
                className="flex items-center gap-1 text-xs font-bold text-indigo-600 hover:text-indigo-800"
              >
                {copiedKey === `curl-${idx}` ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                复制
              </button>
            </div>
            <pre className="overflow-x-auto rounded-[10px] bg-slate-900 px-3 py-2.5 text-[11px] leading-5 text-slate-100"><code>{cmd}</code></pre>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 个人聚合接口区块：一个令牌覆盖当前用户全部已订阅来源，下游按需拉取。
 * 自包含加载/轮换令牌，可直接嵌入「接入集成」。
 */
export default function FeedAccessSection({ showToast }) {
  const [feedToken, setFeedToken] = useState(null);
  const [feedLoading, setFeedLoading] = useState(true);
  const [plainToken, setPlainToken] = useState('');
  const [rotating, setRotating] = useState(false);
  const [docsOpen, setDocsOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState('');

  useEffect(() => {
    let mounted = true;
    fetchFeedToken()
      .then(data => { if (mounted) setFeedToken(data); })
      .catch(error => showToast?.(error.message || '获取聚合接口令牌失败', 'error'))
      .finally(() => { if (mounted) setFeedLoading(false); });
    return () => { mounted = false; };
  }, [showToast]);

  const handleCopy = async (text, key) => {
    try {
      await copyText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(''), 2000);
    } catch {
      showToast?.('复制失败，请手动选择文本复制', 'error');
    }
  };

  const handleRotate = async () => {
    setRotating(true);
    try {
      const result = await rotateFeedToken();
      setPlainToken(result.token);
      setFeedToken({ exists: true, token_preview: result.token_preview });
      showToast?.('已生成新的访问令牌', 'success');
    } catch (error) {
      showToast?.(error.message || '生成访问令牌失败', 'error');
    } finally {
      setRotating(false);
    }
  };

  return (
    <div className="surface-card rounded-[14px] overflow-hidden">
      <div className="flex items-center gap-3 border-b border-slate-100 px-6 py-4">
        <div className="h-5 w-1 rounded-full bg-indigo-500" />
        <h3 className="section-title">个人聚合接口</h3>
        <span className="ml-auto text-xs font-medium text-slate-400">dfeed_ · 覆盖你订阅的全部来源</span>
      </div>

      <div className="space-y-4 p-6">
        <p className="tiny-meta">一个接口覆盖你订阅的全部来源，下游可按发布时间、类型、关键词等自由筛选拉取（与「订阅分发」无关，订阅在阅读器里管理）。</p>

        <div>
          <p className="form-label">接口地址</p>
          <div className="flex items-center gap-2 rounded-[10px] border border-slate-100 bg-slate-50 px-3 py-2">
            <code className="min-w-0 flex-1 truncate text-xs font-bold text-slate-600" title={feedEndpoint()}>{feedEndpoint()}</code>
            <button
              type="button"
              onClick={() => handleCopy(feedEndpoint(), 'feed-endpoint')}
              className="shrink-0 text-slate-400 hover:text-indigo-600"
              title="复制接口地址"
              aria-label="复制接口地址"
            >
              {copiedKey === 'feed-endpoint' ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="tiny-meta">
            {feedLoading
              ? '正在读取令牌状态…'
              : feedToken?.exists
                ? `访问令牌 ${feedToken.token_preview}`
                : '尚未生成访问令牌'}
          </p>
          <button onClick={handleRotate} disabled={rotating} className="action-button action-button-secondary text-xs">
            {rotating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
            {feedToken?.exists ? '重新生成令牌' : '生成访问令牌'}
          </button>
        </div>

        {plainToken && (
          <div className="surface-card rounded-[14px] border-emerald-200 bg-emerald-50/80 p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
              <div className="flex min-w-0 flex-1 items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[10px] bg-emerald-100 text-emerald-700">
                  <KeyRound className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-black text-emerald-900">访问令牌仅显示一次</p>
                  <p className="tiny-meta mt-1 text-emerald-700">复制到你的下游系统，后续只能再次生成新令牌。</p>
                  <code className="mt-2 block break-all rounded-[10px] bg-white/80 px-3 py-2 text-xs font-bold text-emerald-950">{plainToken}</code>
                </div>
              </div>
              <button onClick={() => handleCopy(plainToken, 'token-notice')} className="action-button action-button-secondary shrink-0">
                {copiedKey === 'token-notice' ? <Check /> : <Copy />}
                {copiedKey === 'token-notice' ? '已复制' : '复制令牌'}
              </button>
            </div>
          </div>
        )}

        <button
          type="button"
          onClick={() => setDocsOpen(open => !open)}
          className="flex items-center gap-2 text-sm font-bold text-indigo-600 hover:text-indigo-800"
        >
          {docsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <Terminal className="h-4 w-4" /> 接口文档与调用示例
        </button>
        {docsOpen && <FeedDocsPanel plainToken={plainToken} onCopy={handleCopy} copiedKey={copiedKey} />}
      </div>
    </div>
  );
}
