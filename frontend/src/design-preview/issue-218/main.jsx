import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '../../index.css';
import './r2-preview.css';
import R2Preview from './R2Preview';

// issue #218 R2 交互样页入口(frontend/r2-preview.html):只在开发服务器上按路径打开;
// 生产构建入口仍只有 index.html,不打包、不注册 Service Worker、不请求任何 /api。
createRoot(document.getElementById('root')).render(
  <StrictMode>
    <R2Preview />
  </StrictMode>,
);
