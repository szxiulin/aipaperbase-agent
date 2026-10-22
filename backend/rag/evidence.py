from __future__ import annotations

from raglib import Chunk
from backend.catalog import identity


def chunk_citation(chunk: Chunk, score: float | None = None) -> dict:
    """Turn a retrieved chunk into a citation structure with paper/section evidence (interpreting the metadata)."""
    metadata = chunk.metadata
    citation = {
        "chunk_id": chunk.id,
        "entity_id": identity.canonical(metadata.get("entity_id", chunk.document_id)),
        "title": metadata.get("title", ""),
        "section": metadata.get("section", ""),
        "text": chunk.text,
    }
    if score is not None:
        citation["score"] = round(float(score), 4)
    return citation
