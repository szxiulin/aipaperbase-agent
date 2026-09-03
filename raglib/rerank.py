from __future__ import annotations

from typing import Protocol

from .documents import Chunk


class Reranker(Protocol):
    def rerank(self, query: str, chunks: list[Chunk], top_k: int) -> list[tuple[Chunk, float]]: ...


class OpenAICompatReranker:
    """OpenAI-compatible rerank endpoint (SiliconFlow / DashScope, etc.)."""

    def __init__(self, base_url: str, api_key: str, model: str) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._client = httpx.Client(timeout=60)

    def rerank(self, query: str, chunks: list[Chunk], top_k: int) -> list[tuple[Chunk, float]]:
        if not chunks:
            return []
        response = self._client.post(
            f"{self.base_url}/rerank",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "query": query, "documents": [c.text for c in chunks]},
        )
        response.raise_for_status()
        results = sorted(
            response.json().get("results", []),
            key=lambda item: item.get("relevance_score", 0.0),
            reverse=True,
        )
        return [(chunks[item["index"]], item.get("relevance_score", 0.0)) for item in results[:top_k]]
