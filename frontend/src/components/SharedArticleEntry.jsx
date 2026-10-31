import { lazy, Suspense } from 'react';
import { Loader2 } from 'lucide-react';
import TabErrorBoundary from './TabErrorBoundary';

// 公开分享页的入口壳(issue #230 审计 M-14 · 分享页独立入口):分享页自成一个 chunk,只有打开 #/s/… 的访客
// 才下载它与它要的 Markdown / KaTeX 渲染器;此前 main.jsx 静态引入 SharedArticlePage,把这两样带进了每个人的入口包。
// 不另起 HTML 入口:分享链接形如 /#/s/{token}(后端签发、对外契约),路由仍由 main.jsx 按 hash 岔开。
const SharedArticlePage = lazy(() => import('./SharedArticlePage.jsx'));

// chunk 在途时的占位与分享页自己的「正在打开…」同一版式:数据到达前访客只看到这一种加载态
const FALLBACK = (
  <div className="shared-page shared-page-center">
    <Loader2 className="h-5 w-5 animate-spin" />
    <span className="body-text">正在打开…</span>
  </div>
);

// 分享分支在 App 的错误边界之外:chunk 拉取失败(断网 / 发版后旧资源失效)不能击穿到根白屏(codex 检视第 1 轮 P2)。
// TabErrorBoundary 先「重试」,React.lazy 缓存了拒绝仍失败就变「刷新页面」整页重取;令牌在 hash 里,刷新不丢
export default function SharedArticleEntry({ token }) {
  return (
    <TabErrorBoundary>
      <Suspense fallback={FALLBACK}>
        <SharedArticlePage token={token} />
      </Suspense>
    </TabErrorBoundary>
  );
}
