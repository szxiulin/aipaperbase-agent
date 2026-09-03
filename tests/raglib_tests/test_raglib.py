from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from raglib import (
    Document,
    InMemoryStore,
    MarkdownSplitter,
    Pipeline,
    load_config,
    load_dotenv,
)


class FakeEmbedder:
    model = "fake"
    dim = 3

    def embed_texts(self, texts):
        return [[len(t) % 5, (len(t) + 1) % 5, 1.0] for t in texts]

    def embed_query(self, text):
        return [len(text) % 5, (len(text) + 1) % 5, 1.0]


class FakeReranker:
    def rerank(self, query, chunks, top_k):
        return [(chunk, 0.9 - i * 0.1) for i, chunk in enumerate(chunks[:top_k])]


class FakeGenerator:
    def generate(self, query, chunks, history=None):
        return {"answer": "generated-answer", "finish_reason": "stop", "reasoning": "", "model": "fake"}


class MarkdownSplitterTest(unittest.TestCase):
    def test_split_by_heading(self) -> None:
        splitter = MarkdownSplitter()
        doc = Document(id="d1", text="# 标题\n\n正文内容\n\n## 第二节\n\n更多内容")
        chunks = splitter.split(doc)
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0].metadata["section"], "# 标题")
        self.assertTrue(chunks[0].text.startswith("# 标题"))
        self.assertEqual(chunks[0].document_id, "d1")
        self.assertEqual(chunks[0].id, "d1:0")

    def test_long_section_is_split(self) -> None:
        splitter = MarkdownSplitter(max_chars=40)
        long_body = "\n\n".join(f"段落{i} " + "x" * 20 for i in range(5))
        doc = Document(id="d1", text=f"# 长节\n\n{long_body}")
        chunks = splitter.split(doc)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(c.text.startswith("# 长节") for c in chunks))

    def test_no_headings(self) -> None:
        splitter = MarkdownSplitter()
        chunks = splitter.split(Document(id="d1", text="纯文本没有标题"))
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].metadata["section"], "")


class ConfigTest(unittest.TestCase):
    def test_load_config(self) -> None:
        config = load_config({
            "EMBEDDING_BASE_URL": "https://x",
            "EMBEDDING_MODEL": "m",
            "EMBEDDING_DIM": "4096",
            "RERANKER_MODEL": "r",
            "QDRANT_URL": "http://q",
        })
        self.assertEqual(config.embedder.dim, 4096)
        self.assertEqual(config.embedder.model, "m")
        self.assertEqual(config.reranker.model, "r")

    def test_load_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("A=1\n# 注释\nB='two words'\nC=already-set\n", encoding="utf-8")
            import os
            os.environ["C"] = "existing"
            load_dotenv(str(path))
            self.assertEqual(os.environ["A"], "1")
            self.assertEqual(os.environ["B"], "two words")
            self.assertEqual(os.environ["C"], "existing")  # does not overwrite an existing one


class PipelineTest(unittest.TestCase):
    def test_ingest_and_query(self) -> None:
        pipeline = Pipeline(
            MarkdownSplitter(), FakeEmbedder(), InMemoryStore(),
            reranker=FakeReranker(), generator=FakeGenerator(),
        )
        docs = [
            Document(id="d1", text="# A\n\nfoo bar", metadata={"entity_id": "d1", "title": "T1"}),
            Document(id="d2", text="# B\n\nbaz qux", metadata={"entity_id": "d2", "title": "T2"}),
        ]
        count = pipeline.ingest(docs)
        self.assertEqual(count, 2)

        result = pipeline.query("foo", top_k=2)
        self.assertEqual(result["answer"], "generated-answer")
        self.assertEqual(len(result["chunks"]), 2)

        filtered = pipeline.query("foo", top_k=5, filters={"entity_id": "d1"})
        self.assertTrue(all(c.metadata.get("entity_id") == "d1" for c in filtered["chunks"]))

    def test_query_without_generator(self) -> None:
        pipeline = Pipeline(MarkdownSplitter(), FakeEmbedder(), InMemoryStore())
        pipeline.ingest([Document(id="d1", text="# A\n\nhello world")])
        result = pipeline.query("hello")
        self.assertEqual(result["answer"], "")
        self.assertEqual(len(result["chunks"]), 1)


if __name__ == "__main__":
    unittest.main()
