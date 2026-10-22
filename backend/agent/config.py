from __future__ import annotations

"""Agent config layer: AgentConfig is a read-only config object (aligned with the modern Runner/Agent separation).

Core trade-off (informed by the OpenAI Agents SDK): Agent is "config" (data), Runner is the "executor" (logic).
- The same AgentConfig can be safely reused across concurrent requests; it holds no runtime state;
- Switching model vendor / adjusting budget / changing instructions / adding agents (audit, ingest, ...) only touches config, not the runner;
- coerce() normalization entry: accepts None (defaults from .env), raglib.RAGConfig, or legacy objects with a .generator.
"""

from dataclasses import dataclass, replace

DEFAULT_SYSTEM_PROMPT = """你是「AI 论文研究助手」，帮用户在一个本地 AI 论文库里检索、统计和理解论文。

## 可用的数据
- 本地论文库：30 个顶会顶刊（CVPR、ICCV、ECCV、NeurIPS、ICML、ICLR、ACL、EMNLP、AAAI、TPAMI、IJCV 等），2023–2026 年，去重研究论文约 13.8 万篇。领域有 21 个及“其他”，方向有 383 个；部分论文的领域或方向待分类，不要推断缺失标签。具体数量和分类覆盖率以统计工具返回为准。标题和摘要都是英文。2026 年还在收录中。
- 外部学术源：arXiv（最新预印本）、OpenAlex（引用数、作者、核验出处）。
- 联网搜索（仅当工具列表里有 web_search 时）：查新闻、项目主页、博客、榜单等非论文信息。
- 本地全文库：只有用户下载并入库的论文才有全文，状态用 local_fulltext_status 查看。

## 选工具
- 统计（多少篇、趋势、占比、哪个会议最多、某领域有哪些方向）：用 count_papers / trend_papers / share_papers / top_values，可用 area_names、direction_names 按中文名过滤；不确定名称时先 list_facets(type="areas")。
- 找论文：用 search_papers。query 必须是英文核心词（3–6 个）；中文问题先译成英文术语。一个概念有多种说法时分 2–3 次检索（如 "training-free" / "zero-shot"，"super-resolution" / "upscaling"）。根据 snippet 判断是否真的相关，不相关的不要列出；match.note 表示自动放宽了关键词。
- 单篇细节：get_paper 看摘要；已入库论文用 search_evidence / get_evidence 查全文片段。
- 本地库没有或要最新进展：arxiv_search（英文关键词，sort="date" 看最新）；要引用数或作者信息：openalex_search / openalex_get_work / openalex_get_author。
- 联网：web_search 搜网页，需要正文时 web_fetch。
- 加入集合、下载入库：用 propose_collection_membership / propose_collection_assignments / ingest_papers / arxiv_ingest，它们只生成待用户确认的草稿，不要说“已完成”。

## 回答
- 先给结论，再给依据。用用户提问的语言回答（默认中文），结构清晰；列论文时给出标题、会议和年份，需要时用一句话说明为什么相关。
- 引用工具返回的论文或网页时用 [n]，n 取结果里的 citation_index；不编造论文、作者、数字或链接。
- 没查到就直说，并说明试过哪些检索词；不要把检索候选数说成相关论文数。
- 给统计数字时说明口径：每篇论文只算一次；2026 年未完结，不与完整年份直接比较。
- 同一检索不要重复；证据足够就停止调用工具，直接回答。
"""

DEFAULT_MAX_ROUNDS = 8
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
    max_tokens: int = 8192
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
            from_env = value is None
            if from_env:
                from backend.common import settings
                from raglib.config import load_config

                settings.ensure_loaded()
                value = load_config()
            gen = getattr(value, "generator", None) or value
            cfg = cls(
                model=getattr(gen, "model", "") or "",
                base_url=(getattr(gen, "base_url", "") or "").rstrip("/"),
                api_key=getattr(gen, "api_key", "") or "",
                thinking=getattr(gen, "thinking", "enabled") or "enabled",
                reasoning_effort=getattr(gen, "reasoning_effort", "high") or "high",
                max_tokens=int(getattr(gen, "max_tokens", 8192) or 8192),
            )
            if from_env:
                # Same defaults as the settings page: a key alone is enough to chat with DeepSeek.
                cfg = replace(cfg, base_url=cfg.base_url or settings.current("base_url"),
                              model=cfg.model or settings.current("model"))
        overrides: dict = {}
        if instructions is not None:
            overrides["instructions"] = instructions
        if max_rounds is not None:
            overrides["max_rounds"] = max_rounds
        if top_k is not None:
            overrides["top_k"] = top_k
        return replace(cfg, **overrides) if overrides else cfg


__all__ = ["AgentConfig", "DEFAULT_SYSTEM_PROMPT", "DEFAULT_MAX_ROUNDS", "DEFAULT_TOP_K"]
