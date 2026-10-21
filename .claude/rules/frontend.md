---
paths:
  - "frontend/**"
---

# 前端硬规则(正文:docs/frontend/conventions.md)

- 改任何样式 / UI 前先读 `docs/frontend/conventions.md`;token 与角色类单一事实来源 = `frontend/src/index.css`(`--r-*` / `--motion-*` / `--dorami-*`、`.body-text` / `.micro-label` 等),不手写 `text-[Npx]` / `rounded-[Npx]` / hex。
- 字重只用 400 / 600(早报头条 900),字号下限 12px;界面系统黑体、标题与分数 `Dorami Serif`,英文文本经 `utils/readerText.latinLang` 标 `lang="en"`;守卫 `frontend/test/typography.test.mjs` + eslint。
- 新增动效**不写 OS 媒体查询**,钳制挂 `:root[data-motion="reduce"]`、JS 读 `motionReduced()`、recharts 显式 `isAnimationActive`;登录页品牌演出是豁免。
- 遮罩关闭判定只在 `components/Modal.jsx`,业务代码一律 `<Modal closeOnOverlay>`(lint `dorami/no-raw-modal-overlay`);多层浮层经 `hooks/useModalA11y` 层栈。
- 读者面文案不漏内部架构词(归档 / 采集 / 分发 / 层 / 归档中枢);按 `readerOnly` / `isAdmin` 分文案;口径统一叫「兴趣」。
- 管理面纪律:零描述 / 引导句;列头即操作(`components/admin/TableTh.jsx`);`rowact-btn` 小图标而非文字大按钮;高危操作进抽屉;像素级验收(Playwright 量 bounding box)。
- 容器模型:`content_shape` article / social / podcast 互斥,不可按「非 X 即 Y」二分;社交流不计 reads。
- 交付前 `npm run lint` / `npm test` / `npm run build`,交互改动接 `scripts/check_mobile_reader_e2e.py` 流程并做反向对照。
