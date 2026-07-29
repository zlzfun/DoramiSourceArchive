import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import SharedArticlePage from './components/SharedArticlePage.jsx'
import { ConfirmProvider } from './components/ConfirmDialog.jsx'
import { shareTokenFromHash } from './utils/shareLink'

// 公开分享页(#/s/{token})在 App 之外分流:访客没有账号,不能走 App 的登录门;
// 且 App 的历史锚点会在挂载时 replace 掉 hash,把令牌冲掉。故在根部就岔开,
// 分享页完全不加载工作台的会话检查/运行能力/导航体系。
const shareToken = shareTokenFromHash(typeof window !== 'undefined' ? window.location.hash : '');

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {shareToken ? (
      <SharedArticlePage token={shareToken} />
    ) : (
      <ConfirmProvider>
        <App />
      </ConfirmProvider>
    )}
  </StrictMode>,
)
