# Issue #7 UI Usability Verification Report

- Date: 2026-09-04 (Asia/Shanghai)
- Base: `origin/main@ad422da`
- Branch: `feat/issue-7-ui-usability`
- Test data: disposable SQLite database and isolated backend/Vite ports; no development data was changed.

## Static checks

```bash
cd frontend
npm run lint
npm run build
```

Result: both passed; Vite 8.0.11 transformed 2673 modules.

## Browser E2E

The in-app Chromium browser was exercised at the default 1280px desktop viewport and a reloaded 390×844 responsive viewport, both in the dark theme.

- Opened Reader discovery from the shared “发现更多来源” entry. The default view contained article, bulletin, social and Podcast sources instead of forcing a Podcast-only catalog.
- Selected the Podcast shape filter. It exposed 35 Podcast sources grouped as 6 official, 24 media and 5 individual sources.
- Subscribed to 20VC in the UI, opened the Podcast container and selected a seeded 64-minute episode.
- Confirmed desktop and mobile details show “简介阅读约 1 分钟”, “原节目 1 小时 4 分钟”, “简介初评 / 基于节目简介”, “节目简介 / 来源方提供” and “中文精华”.
- Confirmed the show-notes sentence preserves `$350M` and `$1B` as visible text and produces zero `.katex` nodes. In dark mode the paragraph computed to `rgb(242, 244, 247)` on the `rgb(12, 14, 19)` page surface. A Vite SSR regression probe also confirmed `$E=mc^2$` and the numeric edge case `$2 + 2 = 4$ 2026` still render as two KaTeX inline nodes.
- Opened Node Management and confirmed the shape counts were 40 article, 15 bulletin, 7 social and 35 Podcast nodes. Applying the Podcast filter rendered exactly 35 rows and no article-shape badges.
- Opened the 20VC node inspector and confirmed its feed URL, Podcast RSS channel, one collected episode, 360-minute schedule, enabled state, reader visibility control and manual-run action. Backend schedule tests confirm enabling registers that per-source interval and disabling removes it.
- Browser console inspection returned no errors or warnings.

This report covers the UI-usability slice only. Episode-level premium generation, transcript processing, ASR, derivative publication and TTS remain in the subsequent Issue #7 core packages.
