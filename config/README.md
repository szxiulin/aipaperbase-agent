# Config

Keeps public configuration that users or maintainers may modify and that must be version-controlled.

- `topics.json`: active research-topic tree and evidence rules;
- `methods.json`: method-tag rules, kept independent from research topics;
- `topic_evaluation_reviews_v1.json` / `v2.json`: checked calibration samples;
- `topics/topics-v1-legacy.json`: retained previous topic-tree definition.

Runtime provider settings live in the local `.env` described by `.env.example`.

Secrets, personal paths, and private settings must not go here; keep them in local user configuration.

---

## 中文

这里保存用户或维护者可以修改、且需要纳入版本管理的公共配置。

- `topics.json`：当前使用的研究主题树与证据规则；
- `methods.json`：与研究主题相互独立的方法标签规则；
- `topic_evaluation_reviews_v1.json` / `v2.json`：已复核的分类校准样本；
- `topics/topics-v1-legacy.json`：保留的上一版主题树定义。

运行时服务配置保存在 `.env.example` 所说明的本地 `.env` 中。

密钥、个人路径和私有设置不得放在这里，应保存在本地用户配置中。
