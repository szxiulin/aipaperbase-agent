# Frontend

The frontend presents and composes backend capabilities; it does not recompute paper statistics or maintain a second set of paper facts.

`src/features/` maps to backend business modules (`catalog`, `analytics`, `library`, `rag`, `integrations`); `pages/` composes complete pages, `components/` holds generic components with no business ownership, and `api/` uniformly calls the backend.

The concrete frontend framework will be decided before the frontend module enters development; this directory structure does not presuppose a framework.

---

## 中文

前端负责呈现和组合后端能力，不负责重新计算论文统计或维护另一套论文事实。

`src/features/` 按 `catalog`、`analytics`、`library`、`rag`、`integrations` 对应后端业务模块；`pages/` 组合完整页面，`components/` 保存无业务归属的通用组件，`api/` 统一调用后端。

具体前端框架在前端模块进入开发前决定，本目录结构不预设框架。
