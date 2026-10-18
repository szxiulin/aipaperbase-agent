# Frontend

The frontend is a no-build, vanilla-JavaScript single-page console served directly by `backend.api.server`.

- `index.html`: page structure and navigation;
- `app.js`: routing, chat (conversations, live progress, settings), papers, collections and maintenance pages;
- `atlas.js`: the area map (treemap → area → direction) built on `/api/cap/*`;
- `palette.css`: the visual design layer (colors, type, layout of the main pages);
- `research.js`: research notes, progress, and comparison-table interactions;
- `i18n.js`: Chinese-default / English UI strings;
- `styles.css`: the shared responsive layout;
- `vendor/`: vendored Markdown and KaTeX assets, so the base UI needs no frontend package install.

The browser calls the local `/api/*` endpoints and does not maintain another copy of paper facts or recompute backend statistics.

---

## 中文

前端是无需构建的原生 JavaScript 单页控制台，由 `backend.api.server` 直接提供。

- `index.html`：页面结构与导航；
- `app.js`：路由、对话（会话、实时进度、设置）、论文库、集合和维护页面；
- `atlas.js`：领域地图（树状面积图 → 领域 → 方向），基于 `/api/cap/*`；
- `palette.css`：视觉设计层（主页面的配色、字体与布局）；
- `research.js`：研究笔记、进度和比较表交互；
- `i18n.js`：默认中文、可切英文的界面文本；
- `styles.css`：共享响应式布局；
- `vendor/`：仓库内置的 Markdown 与 KaTeX 静态资源，因此基础界面无需安装前端依赖。

浏览器统一调用本地 `/api/*`，不维护第二份论文事实，也不在前端重新计算后端统计。
