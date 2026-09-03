from __future__ import annotations

from typing import Protocol


class Embedder(Protocol):
    model: str
    dim: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OpenAICompatEmbedder:
    """OpenAI-compatible embedding endpoint (OpenRouter / SiliconFlow / DashScope, etc.)."""

    def __init__(self, base_url: str, api_key: str, model: str, dim: int = 0) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.dim = dim
        self._client = httpx.Client(timeout=60)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict = {"model": self.model, "input": texts}
        if self.dim:
            payload["dimensions"] = self.dim
        response = self._client.post(
            f"{self.base_url}/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        response.raise_for_status()
        data = sorted(response.json().get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in data]

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_texts([text])
        return vectors[0] if vectors else []
