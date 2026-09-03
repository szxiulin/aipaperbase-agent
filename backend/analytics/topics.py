"""Topic classification v2 (topic tree + technical-tag axis).

Design (see docs/模块设计/16_主题分类v2_定案.md):
- Topic tree = what is researched. family → direction/leaf, three levels (family 5 adds a nested
  "image restoration" container).
- Technical tags = how it is done (methods.json, flat table); attach on match, may coexist,
  never participate in topic determination.
- Two-tier word scoring: strong weight 3.0 / weak weight 1.0; title hits × title_multiplier(2.0).
- benchmark whitelist hits: each +3.0, lights up the corresponding leaf (evidence kind=benchmark).
- Leaf threshold defaults to default_threshold(3.0); family/container nodes are pure grouping
  and carry no determining terms.
- Context gate (fix C): an ontology family root may carry anchor_terms (family-level ontology
  anchors, e.g. f2's llm/language model/gpt, f3's language agent/agentic/llm agent). For a
  family with anchors, its leaves' strong/weak/cooccurrence hits must be accompanied by at least
  one family anchor (in title or abstract) to count, otherwise zeroed — this cures cross-domain
  generic-method words like multi-agent/quantization/pruning/KD dragging non-LLM/non-Agent papers
  into f2/f3 (the "generic-word contamination" noted as it's). benchmark hits are not subject to
  the context gate (the benchmark name itself already targets the ontology; the exemption preserves
  true recall).
- primary = the highest-scoring leaf above threshold (statistical basis); extra leaves ≤
  max_extra_topics(2), must be in a different family from primary, and must have non-benchmark
  text evidence (bench-only alone does not form a cross-family extra leaf).
- Task-first gate (structural implementation of R2/R4, decided by score): when the highest score
  of concrete task families (f4..f10) ≥ the highest score of ontology families (f1/f2/f3), the
  ontology family yields (usage goes only into technical tags); when ontology evidence is clearly
  stronger, the ontology family can still be primary.
- Evidence v2: each hit = {signal, field, points, kind: strong|weak|benchmark|cooccurrence}, auditable.

Output:
- entity_topic_assignments: one primary row per entity + at most 2 extra rows (including role column).
- topic_ancestors: leaf/direction → all ancestors mapping (including itself depth=0), for the SQL
  layer to do unified roll-up statistics.
- entity_tech_tags: persisted technical tags (reused for paradigm timeline / word cloud).
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from backend.catalog.database import ROOT


DEFAULT_CONFIG = ROOT / "config" / "topics.json"
DEFAULT_METHODS = ROOT / "config" / "methods.json"
HTML_TAG = re.compile(r"<[^>]+>")
NON_WORD = re.compile(r"[^\w]+", re.UNICODE)

# Concrete task families: families that study "task applications". Any leaf of these families passing threshold triggers the task-first gate.
CONCRETE_FAMILIES = frozenset({
    "f4_nlp_tasks",
    "f5_lowlevel_vision",
    "f6_generation",
    "f7_highlevel_vision",
    "f8_3d",
    "f9_multimodal",
    "f10_speech",
})

STRONG_WEIGHT = 3.0
WEAK_WEIGHT = 1.0
BENCHMARK_WEIGHT = 3.0

EVIDENCE_KINDS = frozenset({"strong", "weak", "benchmark", "cooccurrence"})
ROLE_PRIMARY = "primary"
ROLE_EXTRA = "extra"


@dataclass(frozen=True)
class Cooccurrence:
    label: str
    terms: tuple[str, ...]  # normalized; all must hit within the same field
    weight: float


@dataclass(frozen=True)
class Topic:
    topic_id: str
    parent_topic_id: str
    name: str
    description: str
    sort_order: int
    threshold: float
    strong: tuple[tuple[str, str], ...]      # (original label, normalized)
    weak: tuple[tuple[str, str], ...]
    benchmarks: tuple[tuple[str, str], ...]
    cooccurrences: tuple[Cooccurrence, ...]
    anchor_terms: tuple[str, ...] = ()       # normalized family-level ontology anchors (only on family roots)
    is_leaf: bool = True


@dataclass(frozen=True)
class MethodTag:
    tag: str
    category: str
    terms: tuple[tuple[str, str], ...]  # (original label, normalized)


def _normalize(value: str) -> str:
    value = HTML_TAG.sub(" ", html.unescape(value))
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(NON_WORD.sub(" ", value).split())


def _contains(text: str, term: str) -> bool:
    return f" {term} " in f" {text} "


def _match_anchor(text: str, term: str) -> bool:
    """Anchor-term matching: exact phrase hit, or a single token matching its simple plural form (llm → llms)."""
    if _contains(text, term):
        return True
    if " " not in term and not term.endswith("s") and _contains(text, term + "s"):
        return True
    return False


def _config_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dedupe_pairs(pairs: Iterable[tuple[str, str]]) -> tuple[tuple[str, str], ...]:
    """Deduplicate by normalized form while preserving original order (of same-word variants, keep the first label)."""
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for label, normalized in pairs:
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append((label, normalized))
    return tuple(result)


def load_topics(path: Path = DEFAULT_CONFIG) -> tuple[dict[str, Any], list[Topic]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    raw_topics = config.get("topics")
    if not config.get("version") or not isinstance(raw_topics, list) or not raw_topics:
        raise ValueError("主题配置缺少 version 或 topics")
    ids = [item.get("topic_id", "") for item in raw_topics]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise ValueError("主题 topic_id 必须非空且唯一")
    known = set(ids)
    children: dict[str, int] = {}
    for item in raw_topics:
        parent = item.get("parent_topic_id", "")
        if parent and parent not in known:
            raise ValueError(f"主题 {item['topic_id']} 的父级不存在: {parent}")
        if parent:
            children[parent] = children.get(parent, 0) + 1
    default_threshold = float(config.get("default_threshold", 3.0))

    topics: list[Topic] = []
    for order, item in enumerate(raw_topics):
        topic_id = item["topic_id"]
        parent = item.get("parent_topic_id", "")
        is_leaf = topic_id not in children

        def pairs(key: str) -> tuple[tuple[str, str], ...]:
            labels = [str(value) for value in item.get(key, [])]
            return _dedupe_pairs((label, _normalize(label)) for label in labels)

        strong = pairs("strong")
        weak = pairs("weak")
        benchmarks = pairs("benchmarks")
        cooccurrences: list[Cooccurrence] = []
        for group in item.get("cooccurrences", []):
            terms = tuple(
                dict.fromkeys(normalized for label in group["all"] if (normalized := _normalize(label)))
            )
            if len(terms) < 2:
                raise ValueError(f"主题 {topic_id} 的共现规则至少需要两个词")
            cooccurrences.append(Cooccurrence(
                label=" + ".join(group["all"]),
                terms=terms,
                weight=float(group["weight"]),
            ))
        threshold = float(item.get("threshold", default_threshold))
        anchor_terms: list[str] = []
        anchor_seen: set[str] = set()
        for label in item.get("anchor_terms", []):
            normalized = _normalize(str(label))
            if normalized and normalized not in anchor_seen:
                anchor_seen.add(normalized)
                anchor_terms.append(normalized)
        topics.append(Topic(
            topic_id=topic_id,
            parent_topic_id=parent,
            name=item["name"],
            description=item["description"],
            sort_order=order,
            threshold=threshold,
            strong=strong,
            weak=weak,
            benchmarks=benchmarks,
            cooccurrences=tuple(cooccurrences),
            anchor_terms=tuple(anchor_terms),
            is_leaf=is_leaf,
        ))

    non_leaf = [topic for topic in topics if not topic.is_leaf]
    for topic in non_leaf:
        if topic.strong or topic.weak or topic.benchmarks or topic.cooccurrences:
            raise ValueError(
                f"非叶节点 {topic.topic_id}（族/容器）不允许携带判定词，它是纯分组节点"
            )
    for topic in topics:
        if not topic.anchor_terms:
            continue
        if topic.parent_topic_id:
            raise ValueError(
                f"anchor_terms 只允许出现在族根节点上（{topic.topic_id} 不是族根）"
            )
        if not topic.is_leaf:
            # A family root is always a non-leaf and has already been cleared by the determining-terms check above; here we only need to confirm the anchor itself is non-empty
            continue
        raise ValueError(f"anchor_terms 只允许出现在族根节点上（{topic.topic_id} 是叶节点）")
    return config, topics


def load_methods(path: Path = DEFAULT_METHODS) -> tuple[dict[str, Any], list[MethodTag]]:
    config = json.loads(path.read_text(encoding="utf-8"))
    raw_tags = config.get("tags")
    if not config.get("version") or not isinstance(raw_tags, list) or not raw_tags:
        raise ValueError("方法配置缺少 version 或 tags")
    seen: set[str] = set()
    tags: list[MethodTag] = []
    for item in raw_tags:
        tag = item.get("tag", "")
        category = item.get("category", "")
        if not tag or tag in seen:
            raise ValueError("技术标签 tag 必须非空且唯一")
        seen.add(tag)
        if not category:
            raise ValueError(f"技术标签 {tag} 缺少 category")
        terms = _dedupe_pairs((str(label), _normalize(label)) for label in item.get("terms", []))
        if not terms:
            raise ValueError(f"技术标签 {tag} 的 terms 为空")
        tags.append(MethodTag(tag=tag, category=category, terms=terms))
    return config, tags


# ---------------------------------------------------------------- classification core

def _score_topic(
    topic: Topic,
    normalized_title: str,
    normalized_abstract: str,
    title_multiplier: float,
    *,
    anchor_terms: tuple[str, ...] = (),
    doc_text: str = "",
) -> tuple[float, list[dict[str, Any]]]:
    score = 0.0
    evidence: list[dict[str, Any]] = []

    # Context gate (fix C): for leaves with family anchors, strong/weak/cooccurrence hits must be
    # accompanied by at least one anchor (anywhere in title or abstract) to count; benchmark hits are exempt.
    anchored = (not anchor_terms) or any(_match_anchor(doc_text, term) for term in anchor_terms)

    def hit(tier_pairs: tuple[tuple[str, str], ...], kind: str, weight: float) -> None:
        nonlocal score
        if not anchored:
            return
        for label, normalized in tier_pairs:
            if _contains(normalized_title, normalized):
                points = weight * title_multiplier
                score += points
                evidence.append({"signal": label, "field": "title", "points": round(points, 2), "kind": kind})
            elif _contains(normalized_abstract, normalized):
                score += weight
                evidence.append({"signal": label, "field": "abstract", "points": round(weight, 2), "kind": kind})

    hit(topic.strong, "strong", STRONG_WEIGHT)
    hit(topic.weak, "weak", WEAK_WEIGHT)
    if anchored:
        for group in topic.cooccurrences:
            if all(_contains(normalized_title, term) for term in group.terms):
                points = group.weight * title_multiplier
                score += points
                evidence.append({"signal": group.label, "field": "title", "points": round(points, 2), "kind": "cooccurrence"})
            elif all(_contains(normalized_abstract, term) for term in group.terms):
                score += group.weight
                evidence.append({"signal": group.label, "field": "abstract", "points": round(group.weight, 2), "kind": "cooccurrence"})
    for label, normalized in topic.benchmarks:
        if _contains(normalized_title, normalized):
            points = BENCHMARK_WEIGHT * title_multiplier
            score += points
            evidence.append({"signal": label, "field": "title", "points": round(points, 2), "kind": "benchmark"})
        elif _contains(normalized_abstract, normalized):
            score += BENCHMARK_WEIGHT
            evidence.append({"signal": label, "field": "abstract", "points": round(BENCHMARK_WEIGHT, 2), "kind": "benchmark"})

    return round(score, 2), evidence


def classify(
    title: str,
    abstract: str,
    topics: list[Topic],
    *,
    title_multiplier: float = 2.0,
    max_extra_topics: int = 2,
    concrete_families: frozenset[str] = CONCRETE_FAMILIES,
) -> dict[str, dict[str, Any]]:
    """Run v2 classification on a single paper.

    Returns {topic_id: {"role": primary|extra, "score": float, "evidence": [...]}}, ordered by
    role (primary first) and config order; returns an empty dict when no leaf passes threshold or all are yielded by the gate.
    """
    normalized_title = _normalize(title)
    normalized_abstract = _normalize(abstract)
    if not normalized_title and not normalized_abstract:
        return {}
    doc_text = f"{normalized_title} {normalized_abstract}".strip()

    leaves = [topic for topic in topics if topic.is_leaf]
    by_id = {topic.topic_id: topic for topic in topics}
    family_of: dict[str, str] = {}
    for topic in leaves:
        node = topic
        while node.parent_topic_id:
            node = by_id[node.parent_topic_id]
        family_of[topic.topic_id] = node.topic_id
    family_anchors: dict[str, tuple[str, ...]] = {
        topic.topic_id: topic.anchor_terms
        for topic in topics
        if topic.anchor_terms
    }

    candidates: dict[str, tuple[float, list[dict[str, Any]]]] = {}
    for topic in leaves:
        score, evidence = _score_topic(
            topic,
            normalized_title,
            normalized_abstract,
            title_multiplier,
            anchor_terms=family_anchors.get(family_of[topic.topic_id], ()),
            doc_text=doc_text,
        )
        if score >= topic.threshold:
            evidence.sort(key=lambda item: (-item["points"], item["signal"]))
            candidates[topic.topic_id] = (score, evidence[:16])

    # Task-first gate (structural implementation of R2/R4, decided by score): when the highest
    # score of concrete task families (f4..f10) ≥ that of ontology families (f1/f2/f3), the ontology
    # family yields (LLM/Agent usage goes only into technical tags, avoiding 21→1788-style inflation);
    # conversely, if ontology evidence is clearly stronger, the ontology family can still be primary.
    concrete_scores = [
        payload[0] for topic_id, payload in candidates.items()
        if family_of[topic_id] in concrete_families
    ]
    if concrete_scores:
        other_scores = [
            payload[0] for topic_id, payload in candidates.items()
            if family_of[topic_id] not in concrete_families
        ]
        if max(concrete_scores) >= max(other_scores) if other_scores else True:
            candidates = {
                topic_id: payload
                for topic_id, payload in candidates.items()
                if family_of[topic_id] in concrete_families
            }
    if not candidates:
        return {}

    ordered = sorted(
        candidates.items(),
        key=lambda item: (-item[1][0], by_id[item[0]].sort_order),
    )
    primary_id, (primary_score, primary_evidence) = ordered[0]
    primary_family = family_of[primary_id]
    assignments: dict[str, dict[str, Any]] = {
        primary_id: {
            "role": ROLE_PRIMARY,
            "score": primary_score,
            "evidence": primary_evidence,
        }
    }
    extras = 0
    for topic_id, (score, evidence) in ordered[1:]:
        if extras >= max_extra_topics:
            break
        if family_of[topic_id] == primary_family:
            continue  # same family: keep only the highest-scoring leaf to avoid multi-tag spam within a family
        # Extra leaves must have non-benchmark text evidence: benchmark hits (e.g. COCO/ImageNet in
        # the abstract) only boost primary or same-family leaves, never alone form cross-family multi-labels
        # — otherwise the benchmark name drags papers into unrelated directions (45.2% bench-only noise).
        if evidence and all(item.get("kind") == "benchmark" for item in evidence):
            continue
        assignments[topic_id] = {"role": ROLE_EXTRA, "score": score, "evidence": evidence}
        extras += 1
    return assignments


def scan_tags(
    title: str,
    abstract: str,
    tags: list[MethodTag],
) -> list[dict[str, str]]:
    """Technical-tag scan: title takes priority, attach on match, may coexist; returns deduped tag/category/match_term/match_field."""
    normalized_title = _normalize(title)
    normalized_abstract = _normalize(abstract)
    found: list[dict[str, str]] = []
    for tag in tags:
        for label, normalized in tag.terms:
            if _contains(normalized_title, normalized):
                found.append({
                    "tag": tag.tag,
                    "category": tag.category,
                    "match_term": label,
                    "match_field": "title",
                })
                break
            if _contains(normalized_abstract, normalized):
                found.append({
                    "tag": tag.tag,
                    "category": tag.category,
                    "match_term": label,
                    "match_field": "abstract",
                })
                break
    return found


# ---------------------------------------------------------------- database build writes

def _entity_texts(connection: sqlite3.Connection) -> Iterable[tuple[str, str, str]]:
    rows = connection.execute(
        """SELECT m.entity_id, p.title, p.abstract
           FROM entity_memberships m
           JOIN paper_records p ON p.record_id = m.record_id
           ORDER BY m.entity_id, m.is_canonical DESC, p.record_id"""
    )
    current_id = ""
    titles: list[str] = []
    abstracts: list[str] = []
    for row in rows:
        entity_id = row["entity_id"]
        if current_id and entity_id != current_id:
            yield current_id, "\n".join(dict.fromkeys(titles)), "\n".join(dict.fromkeys(abstracts))
            titles = []
            abstracts = []
        current_id = entity_id
        if row["title"]:
            titles.append(row["title"])
        if row["abstract"]:
            abstracts.append(row["abstract"])
    if current_id:
        yield current_id, "\n".join(dict.fromkeys(titles)), "\n".join(dict.fromkeys(abstracts))


def _insert_topic_ancestors(connection: sqlite3.Connection, topics: list[Topic]) -> None:
    by_id = {topic.topic_id: topic for topic in topics}
    rows: list[tuple[str, str, int]] = []
    for topic in topics:
        node = topic
        depth = 0
        while True:
            rows.append((topic.topic_id, node.topic_id, depth))
            if not node.parent_topic_id:
                break
            node = by_id[node.parent_topic_id]
            depth += 1
    connection.executemany(
        "INSERT INTO topic_ancestors VALUES (?, ?, ?)",
        rows,
    )


def build_topic_analysis(
    connection: sqlite3.Connection,
    catalog_release_id: str,
    created_at: str,
    *,
    config_path: Path = DEFAULT_CONFIG,
    methods_path: Path = DEFAULT_METHODS,
) -> dict[str, object]:
    """Fully rerun v2 topic classification + technical tags, writing assignments/ancestors/tech_tags.

    Called by import_csv.py during the database build; each entity gets one primary + at most 2 cross-family extras.
    """
    config_path = config_path.resolve()
    methods_path = methods_path.resolve()
    config, topics = load_topics(config_path)
    methods_config, method_tags = load_methods(methods_path)
    topic_digest = _config_digest(config_path)
    methods_digest = _config_digest(methods_path)
    version = config["version"]
    methods_version = methods_config["version"]
    run_id = f"topics-{version}-{catalog_release_id}-{topic_digest[:12]}"
    methods_run_id = f"methods-{methods_version}-{catalog_release_id}-{methods_digest[:12]}"

    connection.execute(
        "INSERT INTO analysis_runs VALUES (?, 'topic_classification', ?, ?, ?, ?, 0, 'building')",
        (run_id, version, topic_digest, catalog_release_id, created_at),
    )
    connection.executemany(
        "INSERT INTO topic_definitions VALUES (?, ?, ?, ?, ?, ?)",
        [
            (topic.topic_id, topic.parent_topic_id, topic.name, topic.description, version, topic.sort_order)
            for topic in topics
        ],
    )
    _insert_topic_ancestors(connection, topics)
    connection.execute(
        "INSERT INTO analysis_runs VALUES (?, 'tech_tag_classification', ?, ?, ?, ?, 0, 'building')",
        (methods_run_id, methods_version, methods_digest, catalog_release_id, created_at),
    )

    title_multiplier = float(config.get("title_multiplier", 2.0))
    max_extra_topics = int(config.get("max_extra_topics", 2))

    by_id = {topic.topic_id: topic for topic in topics}
    family_of: dict[str, str] = {}
    for topic in topics:
        if not topic.is_leaf:
            continue
        node = topic
        while node.parent_topic_id:
            node = by_id[node.parent_topic_id]
        family_of[topic.topic_id] = node.topic_id

    assignment_batch: list[tuple[str, str, float, str, str, str, str, str, str, str]] = []
    tag_batch: list[tuple[str, str, str, str, str, str, str]] = []
    classified_entities: set[str] = set()
    family_counts: dict[str, int] = {topic.topic_id: 0 for topic in topics if not topic.parent_topic_id}
    assignment_count = 0
    extra_count = 0
    tag_count = 0
    total_entities = 0

    def flush() -> None:
        nonlocal assignment_count, tag_count
        if assignment_batch:
            connection.executemany(
                """INSERT INTO entity_topic_assignments
                   (entity_id, topic_id, score, evidence_json, classifier_type,
                    classifier_version, run_id, created_at, catalog_release_id, role)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                assignment_batch,
            )
            assignment_count += len(assignment_batch)
            assignment_batch.clear()
        if tag_batch:
            connection.executemany(
                """INSERT INTO entity_tech_tags
                   (entity_id, tag, category, match_term, match_field, run_id, catalog_release_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                tag_batch,
            )
            tag_count += len(tag_batch)
            tag_batch.clear()

    for entity_id, title, abstract in _entity_texts(connection):
        total_entities += 1
        assignments = classify(
            title, abstract, topics,
            title_multiplier=title_multiplier,
            max_extra_topics=max_extra_topics,
        )
        primary_done = False
        for topic_id, payload in assignments.items():
            role = payload["role"]
            if role == ROLE_PRIMARY:
                if primary_done:
                    raise RuntimeError(f"实体 {entity_id} 出现多个 primary")
                primary_done = True
                classified_entities.add(entity_id)
                family_counts[family_of[topic_id]] += 1
            else:
                extra_count += 1
            assignment_batch.append((
                entity_id, topic_id, payload["score"],
                json.dumps(payload["evidence"], ensure_ascii=False, separators=(",", ":")),
                "rules", version, run_id, created_at, catalog_release_id, role,
            ))
        for tag in scan_tags(title, abstract, method_tags):
            tag_batch.append((
                entity_id, tag["tag"], tag["category"],
                tag["match_term"], tag["match_field"], methods_run_id, catalog_release_id,
            ))
        if len(assignment_batch) >= 2_000 or len(tag_batch) >= 2_000:
            flush()
    flush()

    connection.execute(
        "UPDATE analysis_runs SET assignment_count = ?, status = 'ready' WHERE run_id = ?",
        (assignment_count, run_id),
    )
    connection.execute(
        "UPDATE analysis_runs SET assignment_count = ?, status = 'ready' WHERE run_id = ?",
        (tag_count, methods_run_id),
    )
    return {
        "topic_run_id": run_id,
        "topic_config_version": version,
        "topic_assignments": assignment_count,
        "topic_extra_assignments": extra_count,
        "topic_classified_entities": len(classified_entities),
        "topic_total_entities": total_entities,
        "topic_unclassified_entities": total_entities - len(classified_entities),
        "topic_family_counts": family_counts,
        "tech_tag_run_id": methods_run_id,
        "tech_tag_config_version": methods_version,
        "tech_tag_assignments": tag_count,
    }
