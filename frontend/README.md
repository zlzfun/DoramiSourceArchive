# frontend/

React + Vite + Tailwind CSS v4 前端。

- 命令:`npm install` → `npm run dev`(5173,`/api` 代理到后端;`VITE_PROXY_TARGET` 可改目标)/ `npm run lint` / `npm test`(`test/*.test.mjs`)/ `npm run build`。
- **改任何 UI 前先读 [`../docs/frontend/conventions.md`](../docs/frontend/conventions.md)**;设计 token 与角色类单一事实来源是 `frontend/src/index.css`。
- 结构与组件职责见 [`../docs/architecture/code-map.md`](../docs/architecture/code-map.md);E2E 与 PWA 见 `../docs/frontend/`。
