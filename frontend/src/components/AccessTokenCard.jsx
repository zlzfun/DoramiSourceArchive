import { useEffect, useState } from 'react';
import { Check, Copy, KeyRound, Loader2, RotateCw } from 'lucide-react';
import { fetchFeedToken, rotateFeedToken } from '../api';
import { copyText } from '../utils/clipboard';

/**
 * 访问令牌：dfeed_ 令牌覆盖当前用户全部已订阅来源。
 * MCP 工具调用（必填）与个人订阅接口的 HTTP 拉取共用这同一个令牌。
 * variant='hero' 时渲染深色玻璃面板，直接内嵌进「接入集成」顶部紫色 Hero——
 * 既承接 lede 里「一个访问令牌打通三种用法」的说明，又让用户当场取到令牌，避免重复成块。
 * variant='card'（默认）时是独立白卡，保留作其它场景复用。
 */
export default function AccessTokenCard({ showToast, variant = 'card' }) {
  const [feedToken, setFeedToken] = useState(null);
  const [loading, setLoading] = useState(true);
  const [plainToken, setPlainToken] = useState('');
  const [rotating, setRotating] = useState(false);
  const [copiedKey, setCopiedKey] = useState('');

  useEffect(() => {
    let mounted = true;
    fetchFeedToken()
      .then(data => { if (mounted) setFeedToken(data); })
      .catch(error => showToast?.(error.message || '获取访问令牌失败', 'error'))
      .finally(() => { if (mounted) setLoading(false); });
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

  // ── Hero 变体：深色玻璃面板，内嵌进紫色 Hero ───────────────────────
  if (variant === 'hero') {
    return (
      <div className="mt-5 rounded-[var(--r-card)] border border-white/20 bg-white/[0.12] p-4 backdrop-blur">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--r-control)] bg-white/15 text-white">
            <KeyRound className="h-[18px] w-[18px]" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-bold text-white">你的访问令牌</p>
            <p className="truncate text-xs font-medium text-white/70">
              {loading
                ? '正在读取令牌状态…'
                : feedToken?.exists
                  ? `当前 ${feedToken.token_preview}`
                  : '尚未生成，点右侧按钮创建'}
            </p>
          </div>
          <button
            onClick={handleRotate}
            disabled={rotating}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-[var(--r-control)] border border-white/20 bg-white/15 px-3 py-2 text-xs font-bold text-white transition-colors hover:bg-white/25 disabled:opacity-60"
          >
            {rotating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
            {feedToken?.exists ? '重新生成' : '生成令牌'}
          </button>
        </div>
        <p className="mt-2.5 text-xs leading-relaxed text-white/70">
          MCP 工具调用必须携带它，下方个人订阅接口的 HTTP 拉取也共用它；它只返回你已订阅来源的内容，泄露后请及时重新生成（旧令牌随即失效）。
        </p>

        {plainToken && (
          <div className="mt-3 rounded-[var(--r-control)] border border-white/25 bg-white/[0.18] p-3">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs font-black text-white">访问令牌仅显示一次</p>
                <p className="mt-0.5 text-xs text-white/70">复制到你的 MCP 客户端或下游系统，关闭后只能再次生成新令牌。</p>
                <code className="mt-2 block break-all rounded-[var(--r-control)] bg-black/25 px-2.5 py-1.5 micro-label text-white">{plainToken}</code>
              </div>
              <button
                onClick={() => handleCopy(plainToken, 'token-notice')}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-[var(--r-control)] border border-white/25 bg-white/15 px-2.5 py-1.5 text-xs font-bold text-white transition-colors hover:bg-white/25"
              >
                {copiedKey === 'token-notice' ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
                {copiedKey === 'token-notice' ? '已复制' : '复制'}
              </button>
            </div>
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="surface-card rounded-[var(--r-card)] overflow-hidden">
      <div className="flex items-center gap-3 border-b border-[var(--dorami-border)] px-6 py-4">
        <div className="h-5 w-1 rounded-full bg-amber-500" />
        <h3 className="section-title">访问令牌</h3>
        <span className="ml-auto text-xs font-medium text-slate-500">dfeed_ · 覆盖你订阅的全部来源</span>
      </div>

      <div className="space-y-4 p-6">
        <p className="tiny-meta">
          这是你的身份令牌：<strong className="font-bold text-slate-500">MCP 工具调用必须携带它</strong>，下方个人订阅接口的 HTTP 拉取也用它。它只返回你在阅读器里已订阅来源的内容；请妥善保管，泄露后及时重新生成（旧令牌随即失效）。
        </p>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="tiny-meta">
            {loading
              ? '正在读取令牌状态…'
              : feedToken?.exists
                ? `当前令牌 ${feedToken.token_preview}`
                : '尚未生成访问令牌'}
          </p>
          <button onClick={handleRotate} disabled={rotating} className="action-button action-button-secondary text-xs">
            {rotating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCw className="h-3.5 w-3.5" />}
            {feedToken?.exists ? '重新生成令牌' : '生成访问令牌'}
          </button>
        </div>

        {plainToken && (
          <div className="surface-card rounded-[var(--r-card)] border-emerald-200 bg-emerald-50/80 p-4">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
              <div className="flex min-w-0 flex-1 items-start gap-3">
                <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[var(--r-control)] bg-emerald-100 text-emerald-700">
                  <KeyRound className="h-5 w-5" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-black text-emerald-900">访问令牌仅显示一次</p>
                  <p className="tiny-meta mt-1 text-emerald-700">复制到你的 MCP 客户端或下游系统，关闭后只能再次生成新令牌。</p>
                  <code className="mt-2 block break-all rounded-[var(--r-control)] bg-white/80 px-3 py-2 text-xs font-bold text-emerald-950">{plainToken}</code>
                </div>
              </div>
              <button onClick={() => handleCopy(plainToken, 'token-notice')} className="action-button action-button-secondary shrink-0">
                {copiedKey === 'token-notice' ? <Check /> : <Copy />}
                {copiedKey === 'token-notice' ? '已复制' : '复制令牌'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
