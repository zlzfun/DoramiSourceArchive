import { useCallback, useEffect, useState } from 'react';
import { ArrowRight, ExternalLink, Loader2, Link2Off } from 'lucide-react';
import { API_BASE_URL } from '../config';
import { fetchAuthSession, fetchSharedArticle, startGuestSession } from '../api';
import ReaderMarkdown from './ReaderMarkdown';
import { formatPublishDate } from '../utils/datetime';
import { stripDuplicateLeadingHeading } from '../utils/markdownTitle';

/**
 * 公开分享页（免登录只读单篇）
 *
 * 在登录门**之前**渲染（见 App.jsx）：访客没有账号，先弹登录框就等于分享功能不存在。
 *
 * 措辞纪律（同 docs 里 reader-facing copy 口径）：这页面对外，读者可能是完全不了解本
 * 系统的同事——不出现「归档 / 采集 / 分发 / 节点」等内部词，也不暴露站内导航入口。
 * 页面只有三样东西：这一篇、它的出处、一个回原文的链接。
 * 唯一例外（issue #213 游客模式）：游客访问开着、且访客没登录账号时，页尾多一条「在哆啦美里继续逛逛」，
 * 以游客身份进入阅读器（落早报）；总闸关或已登录账号时不出。
 */
export default function SharedArticlePage({ token }) {
  const [state, setState] = useState({ status: 'loading', data: null, error: '' });
  const [guestCta, setGuestCta] = useState(false);
  const [entering, setEntering] = useState(false);

  // 正文图走分享令牌专属的免登录取图端点（护栏与文章端点同套,且只放行这一篇里的图链）——
  // 默认的 /api/media/proxy 对无会话访客是 401,回退直连又被防盗链 CDN 403,整页裂图。
  const resolveImageSrc = useCallback(
    (url) => (url ? `${API_BASE_URL}/public/share/${encodeURIComponent(token)}/media?url=${encodeURIComponent(url)}` : url),
    [token],
  );

  useEffect(() => {
    let cancelled = false;
    setState({ status: 'loading', data: null, error: '' });
    fetchSharedArticle(token)
      .then((data) => { if (!cancelled) setState({ status: 'ok', data, error: '' }); })
      .catch((err) => {
        if (!cancelled) setState({ status: 'error', data: null, error: err.message || '分享链接无效或已失效' });
      });
    return () => { cancelled = true; };
  }, [token]);

  // 游客入口:匿名或已是游客、且总闸开着才出;会话查询失败就不出(分享页照旧只读这一篇)
  useEffect(() => {
    let cancelled = false;
    fetchAuthSession()
      .then((session) => {
        if (cancelled) return;
        const anonymousOrGuest = !session?.authenticated || Boolean(session?.guest);
        setGuestCta(anonymousOrGuest && Boolean(session?.guest_access_enabled));
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  // 进入阅读器:先签游客会话(记入口 share_page),再去掉 #/s/… 整页载入——分享页在 main.jsx 根部分流,
  // 只改 hash 不会换成 App。签发失败也照样跳:App 会按当时的总闸显示登录页或游客入口。
  const enterAsGuest = async () => {
    setEntering(true);
    try {
      await startGuestSession('share_page');
    } catch {
      // 落到 App 再说
    }
    window.location.assign(`${window.location.pathname}${window.location.search}`);
  };

  // 标签页标题跟内容走:收到链接的人开着好几个标签页时能认出这一篇。
  useEffect(() => {
    if (state.status === 'ok' && state.data?.title) document.title = state.data.title;
  }, [state]);

  if (state.status === 'loading') {
    return (
      <div className="shared-page shared-page-center">
        <Loader2 className="h-5 w-5 animate-spin" />
        <span className="body-text">正在打开…</span>
      </div>
    );
  }

  if (state.status === 'error') {
    return (
      <div className="shared-page shared-page-center">
        <Link2Off className="h-7 w-7 shared-empty-icon" />
        <h1 className="shared-empty-title">链接已失效</h1>
        {/* 失效原因刻意不细分(过期/撤销/不存在),与后端同口径 */}
        <p className="shared-empty-hint">这个分享链接可能已过期或被撤销，请向分享者索取新链接。</p>
      </div>
    );
  }

  const article = state.data;
  const body = stripDuplicateLeadingHeading(article.content, article.title);

  return (
    <div className="shared-page">
      <article className="shared-article">
        <header className="shared-head">
          <div className="shared-kicker">{article.source_name}</div>
          <h1 className="shared-title">{article.title || '（无标题）'}</h1>
          <div className="shared-meta">
            {article.publish_date && <span>{formatPublishDate(article.publish_date)}</span>}
            {article.source_url && (
              <a href={article.source_url} target="_blank" rel="noreferrer" className="shared-source-link">
                查看原文 <ExternalLink className="h-3.5 w-3.5" />
              </a>
            )}
          </div>
        </header>
        <div className="shared-body markdown-body">
          {body
            ? <ReaderMarkdown sourceUrl={article.source_url} resolveImageSrc={resolveImageSrc}>{body}</ReaderMarkdown>
            : <p className="shared-empty-hint">这篇内容没有正文，点上方「查看原文」阅读完整内容。</p>}
        </div>
      </article>
      <footer className="shared-foot">
        <span>由 {article.shared_by} 分享</span>
      </footer>
      {guestCta && (
        <div className="guest-share-cta">
          <span><b>还想看更多？</b>早报、文章、播客都能直接逛，不用登录</span>
          <button type="button" onClick={enterAsGuest} disabled={entering} className="action-button action-button-primary min-h-[32px] px-3 text-xs">
            {entering ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
            在哆啦美里继续逛逛
            {!entering && <ArrowRight className="h-3.5 w-3.5" />}
          </button>
        </div>
      )}
    </div>
  );
}
