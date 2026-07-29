import { useCallback, useEffect, useRef, useState } from 'react';
import { Check, Copy, Link2, Loader2, Trash2, X } from 'lucide-react';
import { createArticleShare, fetchArticleShares, revokeArticleShare } from '../api';
import { copyText } from '../utils/clipboard';
import {
  articleDeepLink,
  publicShareLink,
  shareStatusText,
  SHARE_EXPIRY_OPTIONS,
} from '../utils/shareLink';

/**
 * 分享浮层（阅读窗工具条锚定）——初版方案（2026-07-29 数轮重设计后用户拍板回归）
 *
 * 两档并列、主次分明：
 * · **站内链接** —— 主路径，wash 可点行整行即复制，发给有账号的人，零外泄。
 * · **公开链接** —— 虚线分隔之下退一档：组头（micro 标题 + 「无需登录」注记）→
 *   档位 pill + 生成小钮 → 存活链接状态行（过期/触达 + 复制/撤销）。
 *
 * 与初版的三处差异（均为已拍板的修正，非重设计）：
 * ① 文案：无「同事」（开源项目非公司级部署）、无「仅此一篇」；
 * ② 后端一篇一链（rotate）：已有存活链接时再点「生成」= 换新链接、旧链接同刻失效，
 *    状态行因此恒 ≤1 条，不会堆列表；
 * ③ 字号 bug 修复：初版「生成链接」钮被 button{font:inherit} 未分层规则压成阅读窗
 *    大字（用户当时觉得「违和」的正是 bug 渲染），字号/字重现写在 .share-expiry
 *    容器上靠继承穿透（conventions §3）。
 */
export default function ShareMenu({ articleId, onClose, showToast }) {
  const [liveShare, setLiveShare] = useState(null);   // 该篇当前存活的公开链接(至多一条)
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [expiryDays, setExpiryDays] = useState(7);
  const [copiedKey, setCopiedKey] = useState('');
  const [publicEnabled, setPublicEnabled] = useState(true);
  const panelRef = useRef(null);
  const copyTimerRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const data = await fetchArticleShares(articleId);
      const items = Array.isArray(data?.items) ? data.items : [];
      setLiveShare(items.find((s) => s.live) || null);
      setPublicEnabled(data?.public_share_enabled !== false);
    } catch {
      setLiveShare(null);   // 列表拉不到不该挡住「复制站内链接」这条主路径
    } finally {
      setLoading(false);
    }
  }, [articleId]);

  useEffect(() => { load(); }, [load]);

  // 浮层生命周期：点外部或 Esc 关闭（与全站浮层一致）。
  useEffect(() => {
    const onDown = (e) => {
      if (panelRef.current && !panelRef.current.contains(e.target)) onClose?.();
    };
    const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose?.(); } };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
    };
  }, [onClose]);

  const copy = async (text, key, label) => {
    try {
      await copyText(text);
      setCopiedKey(key);
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current);
      copyTimerRef.current = setTimeout(() => setCopiedKey(''), 1800);
      if (label) showToast?.(`已复制${label}`, 'success');
    } catch (err) {
      showToast?.(err.message || '复制失败，请手动选择文本复制', 'error');
    }
  };

  // 生成(或已有链接时换新):后端 rotate 保证一篇一链,旧链接同刻失效
  const handleCreate = async () => {
    setCreating(true);
    try {
      const replaced = Boolean(liveShare);
      const created = await createArticleShare(articleId, expiryDays);
      setLiveShare(created);
      // 生成即复制:点「生成」的意图就是拿到链接,再让人多点一次纯属多余。
      await copy(publicShareLink(created.token), `s${created.id}`);
      showToast?.(replaced ? '已生成新链接并复制，旧链接已失效' : '已生成公开链接并复制', 'success');
    } catch (err) {
      showToast?.(err.message || '生成分享链接失败', 'error');
    } finally {
      setCreating(false);
    }
  };

  const handleRevoke = async () => {
    if (!liveShare) return;
    try {
      await revokeArticleShare(liveShare.id);
      setLiveShare(null);
      showToast?.('已撤销公开链接', 'success');
    } catch (err) {
      showToast?.(err.message || '撤销失败', 'error');
    }
  };

  return (
    <div className="share-menu" ref={panelRef} role="dialog" aria-label="分享这篇内容">
      <div className="share-menu-head">
        <span className="share-menu-title">分享</span>
        <button type="button" className="share-menu-close" onClick={onClose} aria-label="关闭">
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* ① 站内链接:组头(标题+属性注记,与公开区同构)→ wash 可点行整行即复制 */}
      <div className="share-group-head">
        <span className="micro-label">站内链接</span>
        <span className="share-group-note">需要登录</span>
      </div>
      <button
        type="button"
        className="share-primary"
        onClick={() => copy(articleDeepLink(articleId), 'deep', '站内链接')}
      >
        <Link2 className="h-4 w-4 share-primary-icon" />
        <span className="share-primary-label">
          {copiedKey === 'deep' ? '已复制' : '复制链接'}
        </span>
        {copiedKey === 'deep'
          ? <Check className="h-4 w-4 share-ok" />
          : <Copy className="h-3.5 w-3.5 share-primary-copy" />}
      </button>

      {/* ② 公开链接:虚线分隔之下,同构组头 */}
      <div className="share-public">
        <div className="share-group-head">
          <span className="micro-label">公开链接</span>
          <span className="share-group-note">无需登录</span>
        </div>

        {loading ? null : !publicEnabled ? (
          <p className="share-public-off">公开分享已由管理员关闭，可改用上方站内链接。</p>
        ) : (
          <>
            <div className="share-expiry">
              <div className="share-expiry-opts" role="group" aria-label="有效期">
                {SHARE_EXPIRY_OPTIONS.map((opt) => (
                  <button
                    key={String(opt.days)}
                    type="button"
                    className={`share-expiry-btn ${expiryDays === opt.days ? 'is-on' : ''}`}
                    aria-pressed={expiryDays === opt.days}
                    onClick={() => setExpiryDays(opt.days)}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className="share-create"
                onClick={handleCreate}
                disabled={creating}
                title={liveShare ? '生成新链接，旧链接立即失效' : undefined}
              >
                {creating ? <Loader2 className="h-3 w-3 animate-spin" /> : null}
                {liveShare ? '换新链接' : '生成链接'}
              </button>
            </div>

            {liveShare && (
              <div className="share-item">
                <span className="share-item-meta">
                  <span className="share-item-state">{shareStatusText(liveShare)}</span>
                  <span className="share-item-views">
                    {liveShare.view_count > 0 ? `已打开 ${liveShare.view_count} 次` : '尚未被打开'}
                  </span>
                </span>
                <button
                  type="button"
                  className="rowact-btn"
                  onClick={() => copy(publicShareLink(liveShare.token), `s${liveShare.id}`, '公开链接')}
                  aria-label="复制公开链接"
                  title="复制公开链接"
                >
                  {copiedKey === `s${liveShare.id}` ? <Check className="share-ok" /> : <Copy />}
                </button>
                <button
                  type="button"
                  className="rowact-btn share-item-revoke"
                  onClick={handleRevoke}
                  aria-label="撤销公开链接"
                  title="撤销后链接立即失效"
                >
                  <Trash2 />
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
