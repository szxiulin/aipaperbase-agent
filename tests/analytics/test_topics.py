from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path

from backend.analytics import topics as topics_mod
from backend.analytics.topics import (
    classify,
    load_methods,
    load_topics,
    scan_tags,
)
from backend.catalog.database import initialize

ROOT = Path(__file__).resolve().parents[2]
TOPICS_PATH = ROOT / "config" / "topics.json"
METHODS_PATH = ROOT / "config" / "methods.json"

FAMILY_COUNT = 10
LEAF_COUNT = 45


def _names(assignments: dict) -> set[str]:
    return set(assignments)


def _primary(assignments: dict) -> str | None:
    for topic_id, payload in assignments.items():
        if payload["role"] == "primary":
            return topic_id
    return None


class TopicConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config, cls.topics = load_topics(TOPICS_PATH)

    def test_v2_config_is_loaded(self) -> None:
        self.assertEqual(self.config["version"], "topic-tree-v2-20260902")
        self.assertEqual(self.config["title_multiplier"], 2.0)
        self.assertEqual(self.config["default_threshold"], 3.0)
        self.assertEqual(self.config["max_extra_topics"], 2)

    def test_tree_shape(self) -> None:
        families = [t for t in self.topics if not t.parent_topic_id]
        leaves = [t for t in self.topics if t.is_leaf]
        self.assertEqual(len(families), FAMILY_COUNT)
        self.assertEqual(len(leaves), LEAF_COUNT)
        # each node can have only one parent and the parent id must exist
        ids = {t.topic_id for t in self.topics}
        for topic in self.topics:
            if topic.parent_topic_id:
                self.assertIn(topic.parent_topic_id, ids)

    def test_non_leaf_nodes_are_pure_grouping(self) -> None:
        for topic in self.topics:
            if topic.is_leaf:
                continue
            self.assertEqual(topic.strong, ())
            self.assertEqual(topic.weak, ())
            self.assertEqual(topic.benchmarks, ())
            self.assertEqual(topic.cooccurrences, ())

    def test_five_families_are_fully_expanded_to_direction_and_leaf(self) -> None:
        """Families 2/3/5 need three levels (family -> direction/leaf or container -> leaf) to support drill-down."""
        by_id = {t.topic_id: t for t in self.topics}
        for family_id in ("f2_llm_models", "f3_agent_systems", "f5_lowlevel_vision"):
            family = by_id[family_id]
            children = [t for t in self.topics if t.parent_topic_id == family_id]
            self.assertGreaterEqual(len(children), 3, family_id)
            for child in children:
                if not child.is_leaf:
                    grand = [t for t in self.topics if t.parent_topic_id == child.topic_id]
                    self.assertTrue(grand, f"{child.topic_id} 是容器但无子叶")

    def test_restoration_leaves_are_direct_assignable(self) -> None:
        by_id = {t.topic_id: t for t in self.topics}
        for leaf_id in ("rst_denoise", "rst_derain", "rst_deblur", "rst_dehaze", "rst_artifact", "rst_general"):
            leaf = by_id[leaf_id]
            self.assertTrue(leaf.is_leaf)
            self.assertEqual(leaf.parent_topic_id, "restoration_direction")

    def test_strong_weak_and_benchmark_words_are_parsed(self) -> None:
        by_id = {t.topic_id: t for t in self.topics}
        sr = by_id["sr_direction"]
        self.assertIn(("super-resolution", "super resolution"), sr.strong)  # variant spellings normalized and merged
        self.assertIn(("set5", "set5"), sr.benchmarks)
        self.assertTrue(any(label == "×4" for label, _ in sr.weak))
        alignment = by_id["llm_alignment"]
        self.assertTrue(alignment.cooccurrences)

    def test_methods_config_is_parsed(self) -> None:
        methods_config, tags = load_methods(METHODS_PATH)
        self.assertEqual(methods_config["version"], "methods-v2")
        tag_ids = [tag.tag for tag in tags]
        self.assertIn("diffusion", tag_ids)
        self.assertIn("agentic", tag_ids)
        self.assertIn("3d-gaussian", tag_ids)
        for tag in tags:
            self.assertTrue(tag.terms)


class ClassifyGoldenTest(unittest.TestCase):
    """The three old examples from §5 of the finalized document + ontology/method boundaries."""

    @classmethod
    def setUpClass(cls) -> None:
        _, cls.topics = load_topics(TOPICS_PATH)

    def test_example_title_restoration_but_content_sr(self) -> None:
        out = classify(
            "Image restoration using diffusion priors",
            "We propose blind super-resolution with Set5 and Urban100 benchmarks, up to x4 scale.",
            self.topics,
        )
        self.assertEqual(_primary(out), "sr_direction")

    def test_example_sr_paper_using_agent_stays_in_sr(self) -> None:
        out = classify(
            "Quality assessment of super-resolved images with LLM agents",
            "We use an LLM agent to evaluate perceptual quality of super-resolution results on RealSR.",
            self.topics,
        )
        self.assertEqual(_primary(out), "sr_direction")
        self.assertNotIn("agent_mechanism", out)

    def test_example_llm_body_alignment(self) -> None:
        out = classify(
            "Improving language model alignment",
            "We fine-tune a large language model with RLHF and direct preference optimization, evaluated on MT-Bench.",
            self.topics,
        )
        self.assertEqual(_primary(out), "llm_alignment")

    def test_agent_body_paper_classifies_as_agent(self) -> None:
        out = classify(
            "AgentX: a general tool-use agent framework",
            "We design an autonomous agent with planning, memory and tool calling, evaluated on ToolBench.",
            self.topics,
        )
        self.assertEqual(_primary(out), "agent_mechanism")

    def test_method_theory_beats_incidental_concrete_mention(self) -> None:
        """When ontology evidence is clearly stronger, incidental concrete task words must not win (decided by score)."""
        out = classify(
            "Generalization bounds of graph neural networks",
            "We derive convergence analysis and generalization bound for message passing GNNs, tested on point cloud classification.",
            self.topics,
        )
        family = next(t for t in self.topics if t.topic_id == _primary(out)).parent_topic_id
        self.assertEqual(family, "f1_ml_basis")

    def test_concrete_wins_ties_with_body_family(self) -> None:
        out = classify(
            "Autonomous agents for blind super-resolution quality control",
            "We propose an autonomous agent for super-resolution quality assessment and restoration of degraded images.",
            self.topics,
        )
        self.assertEqual(_primary(out), "sr_direction")

    def test_method_words_never_alone_assign(self) -> None:
        """R5: methods like diffusion/flow/transformer are tech tags and do not form a topic on their own."""
        out = classify(
            "Flow matching transformer for scalable generative modeling",
            "We present a rectified flow architecture for efficient training of deep generative models.",
            self.topics,
        )
        self.assertEqual(out, {})

    def test_unrelated_text_is_unclassified(self) -> None:
        out = classify("Nobody expects the Spanish inquisition", "We study medieval history of Spain.", self.topics)
        self.assertEqual(out, {})


class MultiLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, cls.topics = load_topics(TOPICS_PATH)

    def test_cross_family_extra_is_capped_and_deduped(self) -> None:
        out = classify(
            "SRGAN for object detection in low-resolution videos",
            "We apply super-resolution to improve object detection on COCO video frames, evaluated with YOLO and Set5.",
            self.topics,
        )
        extras = [tid for tid, p in out.items() if p["role"] == "extra"]
        self.assertLessEqual(len(extras), 2)
        # extra leaves must be in a different family than the primary
        by_id = {t.topic_id: t for t in self.topics}
        primary = _primary(out)
        primary_family = by_id[primary].parent_topic_id
        for extra in extras:
            self.assertNotEqual(by_id[extra].parent_topic_id, primary_family)

    def test_same_family_only_keeps_top_leaf(self) -> None:
        out = classify(
            "Unified restoration of denoising deblurring and deraining",
            "An all-in-one image restoration model handles noise removal, motion deblur and rain removal on SIDD and GoPro.",
            self.topics,
        )
        leaves = [tid for tid, p in out.items()]
        # all hits are leaves in family 5, so only one primary should remain
        self.assertEqual(len(leaves), 1)
        self.assertEqual(out[leaves[0]]["role"], "primary")

    def test_primary_score_and_evidence_are_auditable(self) -> None:
        out = classify(
            "Blind super-resolution with diffusion priors",
            "We evaluate on RealSR with Set5 and Urban100.",
            self.topics,
        )
        for payload in out.values():
            self.assertGreater(payload["score"], 0)
            self.assertTrue(payload["evidence"])
            for item in payload["evidence"]:
                self.assertIn(item["kind"], {"strong", "weak", "benchmark", "cooccurrence"})
                self.assertIn(item["field"], {"title", "abstract"})
                self.assertGreater(item["points"], 0)
        sr = out["sr_direction"]
        kinds = {item["kind"] for item in sr["evidence"]}
        self.assertIn("benchmark", kinds)
        # title-hit score = term weight x title_multiplier
        title_strong = [item for item in sr["evidence"] if item["kind"] == "strong" and item["field"] == "title"]
        self.assertTrue(all(item["points"] == 6.0 for item in title_strong))

    def test_benchmark_only_hit_never_forms_cross_family_extra(self) -> None:
        """R5 extension: COCO/PASCAL in the abstract can only boost the primary/same-family leaf,
        and must not on its own light up a paper as a cross-family extra leaf (the root fix for the 45.2% extra noise in the V0 rerun)."""
        out = classify(
            "Detecting objects in the wild",
            "We evaluate our detector on COCO and PASCAL VOC datasets.",
            self.topics,
        )
        by_id = {t.topic_id: t for t in self.topics}
        primary = _primary(out)
        # COCO/PASCAL VOC is hv_detection's benchmark, but benchmark alone is not enough
        # to create cross-family extra labels for any other leaf such as hv_recognition / gen_editing
        for topic_id, payload in out.items():
            if payload["role"] == "extra":
                kinds = {item["kind"] for item in payload["evidence"]}
                self.assertTrue(kinds - {"benchmark"}, f"{topic_id} 是 bench-only extra")
                self.assertNotEqual(by_id[topic_id].parent_topic_id, by_id[primary].parent_topic_id)
        if primary:
            self.assertNotIn("gen_editing", out)
            self.assertNotIn("hv_recognition", out)


class ContextGateTest(unittest.TestCase):
    """fix C: method-word hits in ontology families f2/f3 must be accompanied by a family anchor (llm/language model/gpt/agentic...).

    benchmark hits are exempt from the context gate (the dataset name itself points to the ontology); anchors tolerate plurals (llms -> llm).
    Vocabulary tightened: bare multi-agent alone is not f3 evidence; generic quantization/pruning/KD need an anchor context.
    """

    @classmethod
    def setUpClass(cls) -> None:
        _, cls.topics = load_topics(TOPICS_PATH)

    def _family_of(self, topic_id: str) -> str:
        by_id = {t.topic_id: t for t in self.topics}
        node = by_id[topic_id]
        while node.parent_topic_id:
            node = by_id[node.parent_topic_id]
        return node.topic_id

    def _leaf_ids_in_family(self, family_id: str) -> set[str]:
        return {t.topic_id for t in self.topics if t.is_leaf and self._family_of(t.topic_id) == family_id}

    def test_marl_without_llm_anchor_stays_out_of_f3(self) -> None:
        out = classify(
            "Cooperative multi-agent reinforcement learning with agent teams and debate",
            "We study credit assignment for cooperative MARL agents via reward shaping and communication.",
            self.topics,
        )
        f3 = self._leaf_ids_in_family("f3_agent_systems")
        self.assertFalse(set(out) & f3, "无 LLM 锚点的经典 MARL 不得进 f3")

    def test_vision_pruning_without_llm_anchor_stays_out_of_f2(self) -> None:
        out = classify(
            "Pruning vision transformers for efficient image classification",
            "We prune redundant attention heads of ViT and evaluate on ImageNet.",
            self.topics,
        )
        f2 = self._leaf_ids_in_family("f2_llm_models")
        self.assertFalse(set(out) & f2, "无 LLM 锚点的 ViT 剪枝不得进 f2")

    def test_generic_quantization_without_anchor_is_unclassified(self) -> None:
        out = classify(
            "Extreme low-bit quantization of deep convolutional networks",
            "We quantize CNN weights to 2 bits for edge deployment.",
            self.topics,
        )
        self.assertNotIn("llm_efficiency", out)

    def test_llm_quantization_paper_reaches_efficiency_leaf(self) -> None:
        out = classify(
            "Accurate quantization of large language models for deployment",
            "We quantize weights of LLaMA and GPT models down to 4 bits with minimal quality loss.",
            self.topics,
        )
        self.assertEqual(_primary(out), "llm_efficiency")

    def test_multi_agent_debate_with_llm_anchor_classifies_f3(self) -> None:
        out = classify(
            "Multi-agent debate improves factuality in language models",
            "Multiple LLM agents debate their answers to reduce hallucination.",
            self.topics,
        )
        self.assertIn(_primary(out), self._leaf_ids_in_family("f3_agent_systems"))

    def test_anchor_matches_plural_llms(self) -> None:
        # anchor terms must tolerate llms: even if the title only has a strong method word and the anchor appears only as plural LLMs in the abstract, the context gate should pass
        out = classify(
            "A scalable inference acceleration framework",
            "We speed up token generation of LLMs with early-exit and cascades.",
            self.topics,
        )
        self.assertEqual(_primary(out), "llm_efficiency")

    def test_benchmark_hit_is_exempt_from_context_gate(self) -> None:
        # the dataset name itself points to the ontology: without any LLM anchor text, SWE-bench/WebArena can still light up agent_eval_infra
        out = classify(
            "Evaluating autonomous software agents",
            "We assess agent trajectories on SWE-bench and WebArena.",
            self.topics,
        )
        self.assertEqual(_primary(out), "agent_eval_infra")

    def test_family_root_anchors_are_configured(self) -> None:
        by_id = {t.topic_id: t for t in self.topics}
        f2 = by_id["f2_llm_models"]
        f3 = by_id["f3_agent_systems"]
        self.assertTrue(any("llm" in term or "language model" in term for term in f2.anchor_terms))
        self.assertTrue(any("llm" in term or "language model" in term or term == "agentic"
                            for term in f3.anchor_terms))
        # the family root is a pure grouping: no decision words allowed beyond anchors
        self.assertEqual(f2.strong, ())
        self.assertEqual(f3.weak, ())


class AnchorConfigValidationTest(unittest.TestCase):
    """anchor_terms are only allowed on family root nodes (hard-validated by the loader)."""

    def _write_tmp(self, config: dict) -> Path:
        import tempfile
        tmp = Path(tempfile.mkdtemp()) / "topics_tmp.json"
        tmp.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        return tmp

    def test_anchor_on_leaf_is_rejected(self) -> None:
        config = {
            "version": "v", "title_multiplier": 2.0, "default_threshold": 3.0,
            "max_extra_topics": 2,
            "topics": [
                {"topic_id": "f_root", "parent_topic_id": "", "name": "根",
                 "description": "", "anchor_terms": ["llm"]},
                {"topic_id": "leaf_a", "parent_topic_id": "f_root", "name": "叶A",
                 "description": "", "anchor_terms": ["gpt"]},
            ],
        }
        with self.assertRaises(ValueError):
            load_topics(self._write_tmp(config))

    def test_anchor_on_container_is_rejected(self) -> None:
        config = {
            "version": "v", "title_multiplier": 2.0, "default_threshold": 3.0,
            "max_extra_topics": 2,
            "topics": [
                {"topic_id": "f_root", "parent_topic_id": "", "name": "根",
                 "description": ""},
                {"topic_id": "container", "parent_topic_id": "f_root", "name": "容器",
                 "description": "", "anchor_terms": ["agent"]},
                {"topic_id": "leaf_a", "parent_topic_id": "container", "name": "叶A",
                 "description": ""},
            ],
        }
        with self.assertRaises(ValueError):
            load_topics(self._write_tmp(config))

    def test_family_root_anchor_loads(self) -> None:
        config = {
            "version": "v", "title_multiplier": 2.0, "default_threshold": 3.0,
            "max_extra_topics": 2,
            "topics": [
                {"topic_id": "f_root", "parent_topic_id": "", "name": "根",
                 "description": "", "anchor_terms": ["llm", "LLM", "language model"]},
                {"topic_id": "leaf_a", "parent_topic_id": "f_root", "name": "叶A",
                 "description": "", "strong": ["inference acceleration"]},
            ],
        }
        _, topics = load_topics(self._write_tmp(config))
        root = next(t for t in topics if t.topic_id == "f_root")
        self.assertEqual(root.anchor_terms, ("llm", "language model"))  # case/duplicate normalization dedup


class TechTagTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _, cls.method_tags = load_methods(METHODS_PATH)

    def test_tags_are_independent_from_topics(self) -> None:
        """Super-resolution + Agent: the topic stays in sr (see ClassifyGoldenTest); agentic/llm-based appear only as tech tags."""
        found = scan_tags(
            "Quality assessment of super-resolved images with LLM agents",
            "We use an LLM agent to evaluate super-resolution results.",
            self.method_tags,
        )
        tags = {item["tag"] for item in found}
        self.assertIn("agentic", tags)
        self.assertIn("llm-based", tags)
        self.assertNotIn("diffusion", tags)  # the text has no diffusion, so the tag should not appear out of thin air

    def test_title_priority(self) -> None:
        found = scan_tags(
            "Diffusion priors for image restoration",
            "we use a gan discriminator to refine output.",
            self.method_tags,
        )
        diffusion = next(item for item in found if item["tag"] == "diffusion")
        self.assertEqual(diffusion["match_field"], "title")
        gan = next(item for item in found if item["tag"] == "gan")
        self.assertEqual(gan["match_field"], "abstract")


class BuildIntegrationTest(unittest.TestCase):
    """Run build_topic_analysis on an in-memory DB: the full assignments(role)/ancestors/tech_tags pipeline."""

    def _minimal_records(self) -> list[dict[str, object]]:
        records = [
            {
                "record_id": "r-sr-1", "paper_id": "P1", "venue": "CVPR", "venue_type": "conference",
                "year": 2025, "track": "", "title": "Blind super-resolution with diffusion priors",
                "authors": "A; B", "abstract": "We evaluate on RealSR with Set5 and Urban100 benchmarks.",
            },
            {
                "record_id": "r-sr-2", "paper_id": "P1b", "venue": "ICCV", "venue_type": "conference",
                "year": 2026, "track": "", "title": "Blind super-resolution with diffusion priors",
                "authors": "A; B", "abstract": "",
            },
            {
                "record_id": "r-agent-1", "paper_id": "P2", "venue": "ICLR", "venue_type": "conference",
                "year": 2026, "track": "", "title": "AgentX: a general tool-use agent framework",
                "authors": "C", "abstract": "We design an autonomous agent with planning, memory and tool calling, evaluated on ToolBench.",
            },
        ]
        base = {
            "normalized_title": "", "abstract_source_name": "", "abstract_source_url": "",
            "abstract_source_tier": "", "abstract_fetched_at": "", "doi": "", "arxiv_id": "",
            "paper_url": "", "pdf_url": "", "source_name": "test", "source_url": "https://example.com",
            "source_tier": "official", "verification_status": "verified", "list_status": "final",
            "fetched_at": "2026-01-01T00:00:00", "source_file": "data/catalog/conferences/test.csv",
            "imported_at": "2026-01-01T00:00:00", "release_id": "rel-test-1",
        }
        for item in records:
            item.update(base)
            item["normalized_title"] = "".join(
                c for c in str(item["title"]).casefold() if c.isalnum()
            )
        return records

    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize(self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _seed(self) -> None:
        c = self.connection
        records = self._minimal_records()
        with c:
            c.execute(
                "INSERT INTO data_releases VALUES (?, ?, ?, ?, ?, 'ready')",
                ("rel-test-1", "2026-09-01T00:00:00", "d", 1, len(records)),
            )
            c.execute(
                "INSERT INTO source_files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("data/catalog/conferences/test.csv", "rel-test-1", "h", len(records), "CVPR", "conference", 2025, "final"),
            )
            for record in records:
                c.execute(
                    """INSERT INTO paper_records VALUES
                       (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    tuple(record[field] for field in (
                        "record_id", "paper_id", "venue", "venue_type", "year", "track", "title",
                        "normalized_title", "authors", "abstract", "abstract_source_name",
                        "abstract_source_url", "abstract_source_tier", "abstract_fetched_at",
                        "doi", "arxiv_id", "paper_url", "pdf_url", "source_name", "source_url",
                        "source_tier", "verification_status", "list_status", "fetched_at",
                        "source_file", "imported_at", "release_id",
                    )),
                )
            # two entities: r-sr-1/r-sr-2 merged (doi/title), r-agent-1 independent
            c.execute(
                """INSERT INTO paper_entities
                   (entity_id, canonical_record_id, canonical_title, first_year, last_year,
                    record_count, venue_count, entity_status, created_at, release_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("e-sr", "r-sr-1", "Blind super-resolution with diffusion priors", 2025, 2026, 2, 2, "merged", "t", "rel-test-1"),
            )
            c.execute(
                """INSERT INTO paper_entities
                   (entity_id, canonical_record_id, canonical_title, first_year, last_year,
                    record_count, venue_count, entity_status, created_at, release_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("e-agent", "r-agent-1", "AgentX: a general tool-use agent framework", 2026, 2026, 1, 1, "single", "t", "rel-test-1"),
            )
            for record_id, entity_id in (("r-sr-1", "e-sr"), ("r-sr-2", "e-sr"), ("r-agent-1", "e-agent")):
                c.execute(
                    """INSERT INTO entity_memberships
                       (record_id, entity_id, match_method, evidence_value, confidence,
                        decision_status, is_canonical, created_at, release_id)
                       VALUES (?, ?, ?, ?, ?, 'auto_accepted', ?, ?, ?)""",
                    (record_id, entity_id, "doi", "x", "exact", 1 if record_id == "r-sr-1" else 0, "t", "rel-test-1"),
                )
        # memberships need exact is_canonical = 1 only for the canonical record
        c.execute("UPDATE entity_memberships SET is_canonical = 1 WHERE record_id = 'r-sr-1'")
        c.execute("UPDATE entity_memberships SET is_canonical = 0 WHERE record_id = 'r-sr-2'")
        c.execute("UPDATE entity_memberships SET is_canonical = 1 WHERE record_id = 'r-agent-1'")

    def test_build_pipeline_writes_v2_outputs(self) -> None:
        self._seed()
        result = topics_mod.build_topic_analysis(
            self.connection, "rel-test-1", "2026-09-01T00:00:00",
        )
        self.assertEqual(result["topic_classified_entities"], 2)
        self.assertEqual(result["topic_total_entities"], 2)
        self.assertGreater(result["topic_assignments"], 0)

        rows = self.connection.execute(
            "SELECT entity_id, topic_id, role, score FROM entity_topic_assignments ORDER BY entity_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)  # two entities, one primary each, no cross-family extra
        by_entity = {row["entity_id"]: row for row in rows}
        self.assertEqual(by_entity["e-sr"]["topic_id"], "sr_direction")
        self.assertEqual(by_entity["e-sr"]["role"], "primary")
        self.assertEqual(by_entity["e-agent"]["topic_id"], "agent_mechanism")

        # family stats = primary roll-up
        fam = self.connection.execute(
            """SELECT x.ancestor_topic_id AS family_id, COUNT(*) AS n
               FROM entity_topic_assignments a
               JOIN topic_ancestors x ON x.topic_id = a.topic_id
               WHERE a.role = 'primary' AND x.ancestor_topic_id IN
                     ('f5_lowlevel_vision', 'f3_agent_systems')
               GROUP BY x.ancestor_topic_id"""
        ).fetchall()
        counts = {row["family_id"]: row["n"] for row in fam}
        self.assertEqual(counts, {"f5_lowlevel_vision": 1, "f3_agent_systems": 1})

        # tech tags (e-sr has diffusion as a generation method tag)
        tags = self.connection.execute(
            "SELECT entity_id, tag, category FROM entity_tech_tags WHERE entity_id = 'e-sr'"
        ).fetchall()
        tag_ids = {row["tag"] for row in tags}
        self.assertIn("diffusion", tag_ids)

        # both runs are ready
        runs = self.connection.execute(
            "SELECT analysis_type, status FROM analysis_runs WHERE status = 'ready'"
        ).fetchall()
        self.assertEqual({row["analysis_type"] for row in runs}, {"topic_classification", "tech_tag_classification"})


if __name__ == "__main__":
    unittest.main()
