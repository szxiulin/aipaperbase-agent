# Direction taxonomy scripts

These maintainer scripts built the 383 research directions (`config/directions.json`) from the
labeled data. They are kept for reproducibility; running the app does not need them.

How the directions were built:

1. `export_tasks.py` exports each paper's free-text research task from the area labels.
2. The tasks are embedded (Qwen3-Embedding-8B, on a GPU machine) and clustered inside each area.
3. `refine_draft.py` / `apply_area_changes.py` merge, split and reassign clusters into a draft.
4. `name_placeholders.py` asks an LLM to draft names for unnamed clusters; names live in
   `config/direction_names.json` and can be edited by hand.
5. `audit_precision.py` spot-checks that low- and high-dimensional embeddings agree.

The embedding vectors and intermediate drafts are not part of the repository.

---

## 中文

这些是维护者脚本，用于从标注数据生成 383 个研究方向（`config/directions.json`），保留以便复现；运行应用不需要它们。流程：导出任务短语 → 在 GPU 机器上做向量（Qwen3-Embedding-8B）并在领域内聚类 → 合并/拆分/迁移形成草案 → 由大模型起草方向名（可在 `config/direction_names.json` 手改）→ 抽检精度。向量与中间草案不在仓库中。
