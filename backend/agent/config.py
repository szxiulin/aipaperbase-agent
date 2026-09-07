from __future__ import annotations

"""Agent config layer: AgentConfig is a read-only config object (aligned with the modern Runner/Agent separation).

Core trade-off (informed by the OpenAI Agents SDK): Agent is "config" (data), Runner is the "executor" (logic).
- The same AgentConfig can be safely reused across concurrent requests; it holds no runtime state;
- Switching model vendor / adjusting budget / changing instructions / adding agents (audit, ingest, ...) only touches config, not the runner;
- coerce() normalization entry: accepts None (defaults from .env), raglib.RAGConfig, or legacy objects with a .generator.
"""

from dataclasses import dataclass, replace

DEFAULT_SYSTEM_PROMPT = (
    "你是本地 AI 论文科研助手，只基于工具返回的证据回答用户问题。\n"
    "规则：\n"
    "1. 用户问的是本地论文库内容（论文、方法、数据集、方向、会议、年份），先用 search_catalog 找论文；"
    "涉及具体论文内容（方法细节、实验结果）再用 search_evidence 取证据片段。\n"
    "2. 本地库（search_papers）查不到、或需要引用数/外部权威核验/某期刊某年权威数量时，用 openalex_* 工具"
    "（科研检索 API，权威来源）；用户要『最新/还没发表的预印本/把某篇 arXiv 论文下载入库』时用 arxiv_* 工具"
    "（AI 前沿论文首发地）；需要任意网页内容再用 browser_*。\n"
    "3. 没有检索到就明确说'未检索到'，绝不编造论文、作者或结论。\n"
    "4. 回答引用证据时用 [n]，n 必须对应你实际检索到的证据编号（从 1 开始）。\n"
    "5. 回答用中文，结构清晰（分点/表格），引用的论文给出 venue 与年份。\n"
    "6. 问本地全文库数量、某篇是否已入库或本次入库失败项时，必须先调用 local_fulltext_status；"
    "不得由摘要、DOI、arXiv ID 或下载链接推断入库状态。工具结果矛盾时要报告矛盾，不可猜测。\n"
    "7. 区分指定加入与智能分类。用户说把论文加入集合时，使用 local_fulltext_status、list_collections 确定范围和目标，"
    "调用 propose_collection_membership；不判断内容，不要求索引，不执行下载或 embedding。目标不存在用 target_name。"
    "用户明确要求判断哪些属于集合时，读取集合规则及 get_evidence/search_evidence 内容，调用 propose_collection_assignments，"
    "传入依据实际规则和内容的 decisions。证据不足标 review。同篇可属于多个集合。两种操作都只保存待确认草稿，"
    "不承诺已加入；entity_ids 必须保留明确范围，只有用户明确说全部才能 allow_all=true，这几篇范围不明先询问。\n"
    "8. 收敛规则（重要）：同一主题只检索一次，不要重复搜索；每轮最多并行调用 2 个工具；"
    "一旦证据足以回答，必须立即停止工具调用并给出最终回答；若 2 轮检索仍不足，直接说明缺口后作答。\n"
)

DEFAULT_MAX_ROUNDS = 5
DEFAULT_TOP_K = 5


@dataclass(frozen=True)
class AgentConfig:
    """Read-only agent config. All fields have defaults; coerce() normalizes from legacy config objects."""

    name: str = "research-assistant"
    instructions: str = DEFAULT_SYSTEM_PROMPT
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    thinking: str = "enabled"          # enabled / disabled
    reasoning_effort: str = "high"     # low / high / max (only effective when thinking mode is enabled)
    max_tokens: int = 2048
    max_rounds: int = DEFAULT_MAX_ROUNDS
    top_k: int = DEFAULT_TOP_K

    @classmethod
    def coerce(
        cls,
        value=None,
        *,
        instructions: str | None = None,
        max_rounds: int | None = None,
        top_k: int | None = None,
    ) -> "AgentConfig":
        """Normalization entry: accepts None / raglib.RAGConfig / objects with a .generator / AgentConfig.

        When value=None, load the default model config from .env (lazy import raglib to avoid cycles).
        instructions/max_rounds/top_k are explicit overrides (runner runtime args take precedence over config).
        """
        if isinstance(value, AgentConfig):
            cfg = value
        else:
            if value is None:
                from raglib.config import load_config, load_dotenv

                load_dotenv()
                value = load_config()
            gen = getattr(value, "generator", None) or value
            cfg = cls(
                model=getattr(gen, "model", "") or "",
                base_url=(getattr(gen, "base_url", "") or "").rstrip("/"),
                api_key=getattr(gen, "api_key", "") or "",
                thinking=getattr(gen, "thinking", "enabled") or "enabled",
                reasoning_effort=getattr(gen, "reasoning_effort", "high") or "high",
                max_tokens=int(getattr(gen, "max_tokens", 2048) or 2048),
            )
        overrides: dict = {}
        if instructions is not None:
            overrides["instructions"] = instructions
        if max_rounds is not None:
            overrides["max_rounds"] = max_rounds
        if top_k is not None:
            overrides["top_k"] = top_k
        return replace(cfg, **overrides) if overrides else cfg


__all__ = ["AgentConfig", "DEFAULT_SYSTEM_PROMPT", "DEFAULT_MAX_ROUNDS", "DEFAULT_TOP_K"]
