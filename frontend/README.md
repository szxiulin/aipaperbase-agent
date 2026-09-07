# Frontend

The frontend is a no-build, vanilla-JavaScript single-page console served directly by `backend.api.server`.

- `index.html`: page structure and navigation;
- `app.js`: catalog, library, collections, chat, and insights interactions;
- `research.js`: research notes, progress, and comparison-table interactions;
- `i18n.js`: Chinese-default / English UI strings;
- `styles.css`: the shared responsive layout;
- `vendor/`: vendored Markdown and KaTeX assets, so the base UI needs no frontend package install.

The browser calls the local `/api/*` endpoints and does not maintain another copy of paper facts or recompute backend statistics.

---

## 中文

前端是无需构建的原生 JavaScript 单页控制台，由 `backend.api.server` 直接提供。

- `index.html`：页面结构与导航；
- `app.js`：目录、全文库、集合、对话和数据洞察交互；
- `research.js`：研究笔记、进度和比较表交互；
- `i18n.js`：默认中文、可切英文的界面文本；
- `styles.css`：共享响应式布局；
- `vendor/`：仓库内置的 Markdown 与 KaTeX 静态资源，因此基础界面无需安装前端依赖。

浏览器统一调用本地 `/api/*`，不维护第二份论文事实，也不在前端重新计算后端统计。
