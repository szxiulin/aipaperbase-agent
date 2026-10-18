# Config

Keeps public configuration that users or maintainers may modify and that must be version-controlled.

- `topics.json`: the earlier keyword topic tree, kept for the maintenance pages; the main app uses areas/directions;
- `areas.json` / `directions.json`: level-1 area list (areas-v1.5) and level-2 directions (directions-v1.1, 383 directions built from the v4 draft); `direction_names.json` overrides the 119 placeholder direction names;
- `methods.json`: earlier method-tag rules, kept for the maintenance pages;
- `topic_evaluation_reviews_v1.json` / `v2.json`: checked calibration samples;
- `topics/topics-v1-legacy.json`: retained previous topic-tree definition.

Runtime provider settings live in the local `.env` described by `.env.example`.

Secrets, personal paths, and private settings must not go here; keep them in local user configuration.

---

## 中文

这里保存用户或维护者可以修改、且需要纳入版本管理的公共配置。

- `topics.json`：早期的关键词主题树，仅维护页面使用；主应用使用领域/方向；
- `areas.json` / `directions.json`：一级领域列表（areas-v1.5）与二级方向（directions-v1.1，由 v4 草案生成，383 个方向）；`direction_names.json` 为 119 个原占位方向提供正式名称；
- `methods.json`：早期的方法标签规则，仅维护页面使用；
- `topic_evaluation_reviews_v1.json` / `v2.json`：已复核的分类校准样本；
- `topics/topics-v1-legacy.json`：保留的上一版主题树定义。

运行时服务配置保存在 `.env.example` 所说明的本地 `.env` 中。

密钥、个人路径和私有设置不得放在这里，应保存在本地用户配置中。
