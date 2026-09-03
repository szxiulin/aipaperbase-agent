from __future__ import annotations

import math
import uuid
from typing import Protocol

from .documents import Chunk


def _point_id(chunk_id: str) -> str:
    """Convert any string chunk id to a valid Qdrant UUID point id (deterministic)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, chunk_id))


class VectorStore(Protocol):
    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None: ...

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]: ...

    def list_by_document(self, document_id: str) -> list[Chunk]: ...

    def delete(self, document_id: str) -> None: ...


class InMemoryStore:
    """In-memory brute-force cosine retrieval, for tests and environments without Qdrant."""

    def __init__(self) -> None:
        self._chunks: dict[str, Chunk] = {}
        self._vectors: dict[str, list[float]] = {}

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        for chunk, vector in zip(chunks, vectors):
            self._chunks[chunk.id] = chunk
            self._vectors[chunk.id] = vector

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        scored: list[tuple[Chunk, float]] = []
        for chunk_id, vector in self._vectors.items():
            chunk = self._chunks[chunk_id]
            if filters and not all(chunk.metadata.get(k) == v for k, v in filters.items()):
                continue
            scored.append((chunk, self._cosine(query_vector, vector)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def delete(self, document_id: str) -> None:
        for chunk_id in [cid for cid, c in self._chunks.items() if c.document_id == document_id]:
            self._chunks.pop(chunk_id, None)
            self._vectors.pop(chunk_id, None)

    def list_by_document(self, document_id: str) -> list[Chunk]:
        return [c for c in self._chunks.values() if c.document_id == document_id]

    def document_ids(self) -> set[str]:
        return {chunk.document_id for chunk in self._chunks.values()}

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(x * x for x in b))
        return dot / (na * nb) if na and nb else 0.0


class QdrantStore:
    """Qdrant vector store (requires a local or remote Qdrant service)."""

    def __init__(self, url: str, collection: str, vector_size: int) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 qdrant-client，请 `pip install qdrant-client` 并启动 Qdrant") from exc

        self.collection = collection
        self.vector_size = vector_size
        try:
            import httpx
        except ImportError:  # pragma: no cover
            httpx = None
        try:
            self._client = QdrantClient(url=url)
            if not self._client.collection_exists(self.collection):
                self._client.create_collection(
                    collection_name=self.collection,
                    vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
                )
        except Exception as exc:
            connect_error = (httpx is not None and isinstance(exc, httpx.ConnectError)) or isinstance(exc, ConnectionError)
            if connect_error:
                raise RuntimeError(
                    f"无法连接 Qdrant（{url}）：服务未启动。"
                    "请先运行 `docker run -d -p 6333:6333 qdrant/qdrant`，再重试入库。"
                ) from exc
            raise

    def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        from qdrant_client.models import PointStruct

        points = [
            PointStruct(
                id=_point_id(chunk.id),
                vector=vector,
                payload={"chunk_id": chunk.id, "document_id": chunk.document_id, "text": chunk.text, **chunk.metadata},
            )
            for chunk, vector in zip(chunks, vectors)
        ]
        self._client.upsert(collection_name=self.collection, points=points)

    def search(self, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[tuple[Chunk, float]]:
        from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue

        query_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                if isinstance(value, (list, tuple, set)):
                    conditions.append(FieldCondition(key=key, match=MatchAny(any=list(value))))
                else:
                    conditions.append(FieldCondition(key=key, match=MatchValue(value=value)))
            query_filter = Filter(must=conditions)
        response = self._client.query_points(
            collection_name=self.collection,
            query=query_vector,
            query_filter=query_filter,
            limit=top_k,
        )
        result: list[tuple[Chunk, float]] = []
        for point in response.points:
            payload = point.payload or {}
            chunk = Chunk(
                id=payload.get("chunk_id", str(point.id)),
                document_id=payload.get("document_id", ""),
                text=payload.get("text", ""),
                metadata={k: v for k, v in payload.items() if k not in ("chunk_id", "document_id", "text")},
            )
            result.append((chunk, point.score))
        return result

    def delete(self, document_id: str) -> None:
        from qdrant_client.models import FieldCondition, Filter, FilterSelector, MatchValue

        self._client.delete(
            collection_name=self.collection,
            points_selector=FilterSelector(
                filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))])
            ),
        )

    def list_by_document(self, document_id: str) -> list[Chunk]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        items: list[Chunk] = []
        offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=self.collection,
                scroll_filter=Filter(must=[FieldCondition(key="document_id", match=MatchValue(value=document_id))]),
                limit=500,
                offset=offset,
                with_payload=True,
            )
            for point in points:
                payload = point.payload or {}
                items.append(Chunk(
                    id=payload.get("chunk_id", str(point.id)),
                    document_id=payload.get("document_id", ""),
                    text=payload.get("text", ""),
                    metadata={k: v for k, v in payload.items() if k not in ("chunk_id", "document_id", "text")},
                ))
            if next_offset is None:
                break
            offset = next_offset
        return items

    def document_ids(self) -> set[str]:
        ids: set[str] = set()
        offset = None
        while True:
            points, next_offset = self._client.scroll(
                collection_name=self.collection,
                limit=1000,
                offset=offset,
                with_payload=["document_id"],
            )
            for point in points:
                doc_id = (point.payload or {}).get("document_id", "")
                if doc_id:
                    ids.add(doc_id)
            if next_offset is None:
                break
            offset = next_offset
        return ids
