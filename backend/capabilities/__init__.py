"""Capability registry: the single business entry point for HTTP, MCP and the web agent."""
from __future__ import annotations

from backend.capabilities import catalog, stats, taxonomy, topics
from backend.capabilities.registry import Capability, CapabilityRegistry

_FILTERS = {
    "type": "object",
    "description": "Area/direction/venue/year/keyword filters; all optional and AND-combined.",
    "properties": {
        "area_ids": {"type": "array", "items": {"type": "string"},
                     "description": "area ids, e.g. ml_foundations, llm, agents, trustworthy_ai, cv_lowlevel, multimodal"},
        "area_names": {"type": "array", "items": {"type": "string"},
                       "description": "area names (Chinese/English, partial match), e.g. ['智能体'] or ['大语言模型']; use this when you do not know the id"},
        "direction_ids": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['cv_lowlevel-d15-video']"},
        "direction_names": {"type": "array", "items": {"type": "string"},
                            "description": "direction names (Chinese/English, partial match), e.g. ['视频超分']; use this when you do not know the id"},
        "venues": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['CVPR','ICCV']"},
        "years": {"type": "array", "items": {"type": "integer", "minimum": 2023, "maximum": 2026}},
        "query": {"type": "string", "description": "case-insensitive substring on title/abstract/authors"},
        "pending_direction": {"type": "boolean", "description": "Only papers with an assigned area and direction still pending"},
    },
    "additionalProperties": False,
}


def build_registry() -> CapabilityRegistry:
    caps = [
        Capability("search_papers", "Search papers with area/direction/venue/year/keyword filters; returns entity ids and labels.",
                   {"type": "object", "properties": {"filters": _FILTERS,
                    "sort": {"type": "string", "enum": ["year_desc", "title"]},
                    "page": {"type": "integer", "minimum": 1},
                    "page_size": {"type": "integer", "minimum": 1, "maximum": 50}},
                    "additionalProperties": False},
                   catalog.search_papers, "search", "total + items[] with area_id/direction_id"),
        Capability("get_paper", "Fetch one paper with its labels, provenance and venue appearances.",
                   {"type": "object", "properties": {"entity_id": {"type": "string"}},
                    "required": ["entity_id"], "additionalProperties": False},
                   catalog.get_paper, "search", "paper + labels + appearances[]"),
        Capability("sample_papers", "Return the most recent papers under a filter (for examples/drill-down, not a random sample).",
                   {"type": "object", "properties": {"filters": _FILTERS,
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20}}, "additionalProperties": False},
                   catalog.sample_papers, "search", "items[] + note"),
        Capability("count_papers", "Count papers under a filter, optionally grouped by year/venue/area/direction.",
                   {"type": "object", "properties": {"filters": _FILTERS,
                    "group_by": {"type": "string", "enum": ["year", "venue", "area", "direction"]}},
                    "additionalProperties": False},
                   stats.count_papers, "stats", "total (+ items[] when group_by)"),
        Capability("share", "Share of the filtered papers within all papers, grouped by year/venue/area/direction.",
                   {"type": "object", "properties": {"filters": _FILTERS,
                    "by": {"type": "string", "enum": ["year", "venue", "area", "direction"]}},
                    "additionalProperties": False},
                   stats.share, "stats", "items[] with count/denominator/share_rate"),
        Capability("trend", "Yearly counts and share for a filter, plus first-to-last change.",
                   {"type": "object", "properties": {"filters": _FILTERS}, "additionalProperties": False},
                   stats.trend, "stats", "items[] by year + growth"),
        Capability("compare", "Compare 2-4 filters by year (counts and totals).",
                   {"type": "object", "properties": {"groups": {"type": "array", "minItems": 2, "maxItems": 4,
                    "items": {"type": "object", "properties": {"label": {"type": "string"}, "filters": _FILTERS},
                              "required": ["label"]}}}, "required": ["groups"], "additionalProperties": False},
                   stats.compare, "stats", "series[] + by_year[]"),
        Capability("top_values", "Top venues/areas/directions under a filter.",
                   {"type": "object", "properties": {"filters": _FILTERS,
                    "field": {"type": "string", "enum": ["venue", "area", "direction"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False},
                   stats.top_values, "stats", "items[] with share_rate"),
        Capability("cooccurrence", "Count pairs of area/direction/venue/year under a filter.",
                   {"type": "object", "properties": {"filters": _FILTERS,
                    "field_a": {"type": "string", "enum": ["year", "venue", "area", "direction"]},
                    "field_b": {"type": "string", "enum": ["year", "venue", "area", "direction"]},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 500}}, "additionalProperties": False},
                   stats.cooccurrence, "stats", "items[] of {a,b,count}"),
        Capability("recall_topic", "Recall papers for an ad-hoc topic by keyword + structured filters; returns candidates, distribution and a method note (no LLM verification).",
                   {"type": "object", "properties": {"query": {"type": "string", "description": "topic keywords, e.g. 'agent image restoration'"},
                    "filters": _FILTERS, "limit": {"type": "integer", "minimum": 1, "maximum": 200}},
                    "required": ["query"], "additionalProperties": False},
                   topics.recall_topic, "topic", "candidate_count + items[] + stats + verification.status"),
        Capability("list_taxonomy", "List areas and their directions with paper counts; optional area filter.",
                   {"type": "object", "properties": {"area_id": {"type": "string"}}, "additionalProperties": False},
                   taxonomy.list_taxonomy, "taxonomy", "label + areas[] with directions[]"),
    ]
    return CapabilityRegistry(caps)


REGISTRY = build_registry()
