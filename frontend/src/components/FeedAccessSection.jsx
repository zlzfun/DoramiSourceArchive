import { useState } from 'react';
import { Check, ChevronDown, ChevronRight, Copy, Terminal } from 'lucide-react';
import { API_BASE_URL } from '../config';
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

function FeedDocsPanel({ onCopy, copiedKey }) {
  const token = TOKEN_PLACEHOLDER;
  const examples = [
    ['拉取最新（默认 100 条）', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint()}"`],
    ['按发布时间筛选（日报）', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint('?publish_date_start=2026-05-20&publish_date_end=2026-05-26')}"`],
    ['指定类型 + 仅元数据', `curl -H "Authorization: Bearer ${token}" \\\n  "${feedEndpoint('?content_types=rss_article&include_content=false')}"`],
    ['Markdown 批量导出', `curl -H "Authorization: Bearer ${token}" \\\n  "${apiRoot()}/public/feed/articles.md"`],
  ];
  return (
    <div className="mt-4 space-y-4 border-t border-[var(--dorami-border)] pt-4">
      <p className="tiny-meta">下例中的 <code className="font-mono">{TOKEN_PLACEHOLDER}</code> 请替换为上方「访问令牌」里的 dfeed_ 令牌。</p>
      <div>
        <p className="form-label mb-2">请求参数</p>
        <div className="overflow-hidden rounded-[var(--r-control)] border border-[var(--dorami-border)]">
          <table className="w-full text-left text-xs">
            <tbody className="divide-y divide-[var(--dorami-border)]">
              {FEED_PARAMS.map(([name, desc]) => (
                <tr key={name} className="align-top">
                  <td className="w-[220px] bg-[var(--dorami-soft)] px-3 py-2 font-mono font-bold text-slate-500">{name}</td>
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
                className="flex items-center gap-1 text-xs font-bold text-amber-600 hover:text-amber-700"
              >
                {copiedKey === `curl-${idx}` ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                复制
              </button>
            </div>
            <pre className="overflow-x-auto rounded-[var(--r-control)] bg-slate-900 px-3 py-2.5 text-xs leading-5 text-slate-100"><code>{cmd}</code></pre>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * 个人订阅接口区块：面向脚本/自动化的 HTTP 拉取接口，覆盖当前用户全部已订阅来源。
 * 鉴权用上方「访问令牌」公共区的 dfeed_ 令牌（本区块不再单独管理令牌）。
 */
export default function FeedAccessSection({ showToast }) {
  const [docsOpen, setDocsOpen] = useState(false);
  const [copiedKey, setCopiedKey] = useState('');

  const handleCopy = async (text, key) => {
    try {
      await copyText(text);
      setCopiedKey(key);
      setTimeout(() => setCopiedKey(''), 2000);
    } catch {
      showToast?.('复制失败，请手动选择文本复制', 'error');
    }
  };

  return (
    <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
      <div className="flex items-center gap-3 border-b border-[var(--dorami-border)] px-6 py-4">
        <div className="h-5 w-1 rounded-full bg-amber-500" />
        <h3 className="section-title">个人订阅接口</h3>
        <span className="ml-auto text-xs font-medium text-slate-500">dfeed_ · HTTP 拉取</span>
      </div>

      <div className="space-y-4 p-6">
        <div>
          <div className="mb-2 flex items-center justify-between">
            <p className="form-label mb-0">接口地址</p>
            <button
              type="button"
              onClick={() => handleCopy(feedEndpoint(), 'feed-endpoint')}
              className="action-button action-button-quiet min-h-[28px] px-2 text-xs"
            >
              {copiedKey === 'feed-endpoint' ? <Check className="w-3 h-3 text-emerald-500" /> : <Copy className="w-3 h-3" />}
              {copiedKey === 'feed-endpoint' ? '已复制' : '复制'}
            </button>
          </div>
          <code className="block rounded-xl bg-slate-950 px-4 py-3 text-sm font-mono text-slate-300 break-all select-all">
            {feedEndpoint()}
          </code>
          <p className="tiny-meta mt-1.5">用上方「访问令牌」里的 dfeed_ 令牌作 Bearer 鉴权即可调用，只返回你已订阅来源的内容。</p>
        </div>

        <button
          type="button"
          onClick={() => setDocsOpen(open => !open)}
          className="flex items-center gap-2 text-sm font-bold text-amber-600 hover:text-amber-700"
        >
          {docsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          <Terminal className="h-4 w-4" /> 接口文档与调用示例
        </button>
        {docsOpen && <FeedDocsPanel onCopy={handleCopy} copiedKey={copiedKey} />}
      </div>
    </div>
  );
}
