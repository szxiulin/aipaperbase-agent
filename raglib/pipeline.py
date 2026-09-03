from __future__ import annotations

from .documents import Document
from .embed import Embedder
from .generate import Generator
from .rerank import Reranker
from .retrieve import Retriever
from .split import Splitter
from .store import VectorStore


class Pipeline:
    """Tie together chunking, embedding, storage, retrieval, and generation into a reusable ingest / query pipeline."""

    def __init__(
        self,
        splitter: Splitter,
        embedder: Embedder,
        store: VectorStore,
        reranker: Reranker | None = None,
        generator: Generator | None = None,
    ) -> None:
        self.splitter = splitter
        self.embedder = embedder
        self.store = store
        self.retriever = Retriever(embedder, store, reranker)
        self.generator = generator

    def ingest(self, documents: list[Document]) -> int:
        all_chunks = []
        for document in documents:
            all_chunks.extend(self.splitter.split(document))
        if all_chunks:
            vectors = self.embedder.embed_texts([chunk.text for chunk in all_chunks])
            self.store.upsert(all_chunks, vectors)
        return len(all_chunks)

    def query(self, text: str, top_k: int = 5, filters: dict | None = None, history: list[dict] | None = None) -> dict:
        chunks = self.retriever.retrieve(text, top_k, filters)
        answer = ""
        finish_reason = "stop"
        reasoning = ""
        model = ""
        if self.generator:
            result = self.generator.generate(text, chunks, history)
            answer = result.get("answer") or ""
            finish_reason = result.get("finish_reason") or "stop"
            reasoning = result.get("reasoning") or ""
            model = result.get("model") or getattr(self.generator, "model", "") or ""
        return {
            "chunks": chunks,
            "answer": answer,
            "finish_reason": finish_reason,
            "reasoning": reasoning,
            "model": model,
        }
