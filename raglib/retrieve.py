from __future__ import annotations

from .documents import Chunk
from .embed import Embedder
from .rerank import Reranker
from .store import VectorStore


class Retriever:
    """Embed the query → hybrid/vector retrieval → rerank → return top_k chunks."""

    def __init__(self, embedder: Embedder, store: VectorStore, reranker: Reranker | None = None) -> None:
        self.embedder = embedder
        self.store = store
        self.reranker = reranker

    def retrieve(self, query: str, top_k: int = 5, filters: dict | None = None) -> list[Chunk]:
        return [chunk for chunk, _ in self.retrieve_scored(query, top_k, filters)]

    def retrieve_scored(self, query: str, top_k: int = 5, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        query_vector = self.embedder.embed_query(query)
        if not query_vector:
            return []
        hits = self.store.search(query_vector, top_k=max(top_k * 4, top_k), filters=filters)
        if self.reranker and hits:
            hits = self.reranker.rerank(query, [chunk for chunk, _ in hits], top_k)
        return hits[:top_k]
