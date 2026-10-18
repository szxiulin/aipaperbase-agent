# 论文领域打标任务（给 Codex 等编程 Agent）

## 全量（areas-v1.2，244 片，共 121,742 篇）

全量的任务说明在 `scripts/label/codex_shard_prompt.md`，每一片单独开一个全新的 Codex 会话，由脚本自动循环执行：

```bash
.venv/bin/python scripts/label/label_papers.py --all --export data/analyses/codex_full_account_v2 --shard-size 500   # 已导出可跳过
PARALLEL=8 scripts/label/run_codex_shards.sh data/analyses/codex_full_account_v2   # 8 个 Codex 会话并行
```

- 只使用官方 ChatGPT 登录的 Codex 账户额度。监管优先直接调用官方二进制，固定 `CODEX_HOME=~/.codex`、`model_provider=openai`、`forced_login_method=chatgpt`，移除子进程中的 API 认证覆盖变量；启动前通过 `login status` 核实登录方式。不修改全局 CLI 包装脚本，不读取或复制凭证，不回退到 API 或第三方服务。
- 运行模型固定为 `gpt-5.6-sol / medium`，可用 `CODEX_MODEL`、`CODEX_EFFORT` 显式指定。已有运行的模型、强度、冻结分类规则、服务渠道和登录方式必须保持一致，否则需另开工作目录。
- 2026-10-15 已完成官方账户连通测试：`provider: openai`、`gpt-5.6-sol / medium`，成功返回 `READY`。先前 `codex_full/` 的 TeamoRouter 部分结果原样保留；账户运行使用 `codex_full_account/`，只复制相同输入和冻结规则，不混入旧标签。
- 账户第一轮的首 3 片字段校验通过，但副领域比例为 69.8%、47.2%、74.0%，语义检查未放行，4,120 条原始标签保留。已补强每批可见的规则提醒，强调独立跨领域贡献、避免按工具补副领域，不设置比例硬配额；冻结规则、模型和输出字段不变。20 篇诊断复测副领域从 16/20 降为 1/20，保留有依据的双领域项；这不代表全量准确率。新全量使用 `codex_full_account_v2/`，不混入前轮标签，也要重新通过首 3 片检查。论文目录中还发现无摘要的评审名单记录，标为低置信度，未删除。
- `PARALLEL` 默认 8；遇到明确 429/限流错误，监管进程会停止自己启动的 worker、保留进度并降到 4 路重试。首小时无此类错误且质量检查通过后可增至 12 路。
- 出现服务商钱包余额错误时立即停机，保存 `blocked_external_service` 状态及全部已有标签；账户任务应核对渠道配置，不要求第三方充值。CLI 配置错误也立即停止分配，避免遍历所有分片做无效重试。
- 账户额度耗尽时保留进度并停止，等待额度恢复；不自动购买或使用额度重置。普通请求限流可降并发重试，累计 3 次后停止，避免无限循环。
- 每片使用 `codex_shard_prompt.md`：每批 20 篇，必须读完标题与摘要；`next` 附带固定规则复核提醒，不推断标签。worker 用紧凑数组提交亲自决定的标签，`shard_io.py append` 只校验、复制 ID 并展开为标准 JSONL，不做分类。协议版本变化需另开工作目录。
- 断点续跑：完整且字段有效的片会跳过；单片累计最多尝试 4 次。文件锁由操作系统释放，不删除锁文件。每个 worker 超过 1 小时会保留部分结果并重试。
- 前 3 片完成后暂停分配新片，等待人工语义抽查。抽查通过后，在工作目录的 `quality_gate.json` 记录 `approved: true`、`prompt_sha256` 和至少 3 个 `reviewed_shards` 名称；已有 worker 可以完成当前分片。
- `pipeline_state.json` 保存 PID、实际模型、规则摘要、进度、尝试次数与速度。监管进程持续运行，全部 244 片有效且覆盖恰好 121,742 个唯一 ID 后，自动合并并验证 `areas-v1.2.csv`，不会覆盖已有合并文件。
- 实测（2026-10-15，gpt-5.6-sol，medium）：10 篇用时 45 秒，约 2.9 万 token。
- 每片的 Codex 输出记在 `shard_NNNN/codex.log`。
- Worker 关闭无关插件、MCP、记忆和子 Agent 功能，只读取分类规则及本片论文，使用现有官方账户登录，不读取项目 `.env`。
- 全部完成后合并并校验：

```bash
.venv/bin/python scripts/label/label_papers.py --ingest data/analyses/codex_full_account_v2/shard_*/labels.jsonl \
    --model codex-<模型名> --out data/labels/areas-v1.2.csv
```

## 试点（500 篇，已完成）

在仓库根目录启动 Agent，把下面"任务"一节原样发给它。先用 500 篇试点评估质量，再决定要不要扩大规模。

准备输入（已生成过可跳过）：

```bash
.venv/bin/python scripts/label/label_papers.py --sample 500 --seed 42 --export data/analyses/codex_pilot
```

Agent 完成后，校验并对比：

```bash
.venv/bin/python scripts/label/label_papers.py --ingest data/analyses/codex_pilot/labels.jsonl \
    --model codex-<模型名> --out data/labels/pilot/codex_pilot500.csv
.venv/bin/python scripts/label/compare_labels.py data/labels/pilot/areas-v1.1_pilot500.csv \
    data/labels/pilot/codex_pilot500.csv --disagreements data/analyses/codex_vs_deepseek.csv
```

---

## 任务

You are labeling AI research papers for a bibliometric database. **You are the labeler**: read each paper's title and abstract yourself and decide its labels with your own judgment.

**Inputs**
- `data/analyses/codex_pilot/labeling_prompt.md`: the labeling guideline, including the area list, decision rules, and output fields. Read it fully first and follow it exactly.
- `data/analyses/codex_pilot/papers.jsonl`: 500 papers, one JSON object per line (`entity_id`, `venue`, `year`, `title`, `abstract`).

**Output**
- Append one JSON object per paper to `data/analyses/codex_pilot/labels.jsonl`, one per line, in this exact shape:
  `{"entity_id": "...", "area": "...", "area_secondary": "", "area_confidence": "high", "task": "...", "method_family": [], "datasets": [], "modality": [], "application": "", "contribution_type": "method"}`
- `entity_id` must be copied unchanged from the input. Area ids, modalities, and contribution types must come from the lists in the guideline.

**Hard rules**
1. Do NOT write a script, regex, keyword rules, or a classifier that assigns labels, and do NOT call any external LLM or API. Every label must come from your own reading of that paper. Scripts are allowed only for mechanical I/O, such as printing the next batch of papers or appending your answers to the output file.
2. Work in batches of about 25 papers: print a batch, read it, write the 25 labels, then continue. Do not skip papers and do not label from the title alone when an abstract is available.
3. The task is resumable: before starting, count the `entity_id`s already in `labels.jsonl` and continue from the first unlabeled paper. Never write duplicates.
4. Do not modify any other file in the repository.
5. When done, verify that `labels.jsonl` has exactly 500 unique `entity_id`s matching the input and that every line is valid JSON. Then report the count and the area distribution.
