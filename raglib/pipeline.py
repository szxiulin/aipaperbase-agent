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
        chunks_by_document = []
        all_chunks = []
        for document in documents:
            chunks = self.splitter.split(document)
            chunks_by_document.append((document.id, chunks))
            all_chunks.extend(chunks)
        vectors = self.embedder.embed_texts([chunk.text for chunk in all_chunks]) if all_chunks else []
        if chunks_by_document:
            offset = 0
            for document_id, chunks in chunks_by_document:
                document_vectors = vectors[offset : offset + len(chunks)]
                offset += len(chunks)
                # Embedding completes before replacement, so an embedding failure leaves
                # the existing index intact. A vector-store replacement is not transactional,
                # so retain the old chunks for a best-effort compensating restore on write error.
                old_chunks = self.store.list_by_document(document_id)
                self.store.delete(document_id)
                try:
                    if chunks:
                        self.store.upsert(chunks, document_vectors)
                except Exception:
                    if old_chunks:
                        try:
                            self.store.delete(document_id)  # remove a possible partial new write
                            old_vectors = self.embedder.embed_texts([chunk.text for chunk in old_chunks])
                            self.store.upsert(old_chunks, old_vectors)
                        except Exception:
                            pass  # preserve the original write error for the caller to retry
                    raise
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
