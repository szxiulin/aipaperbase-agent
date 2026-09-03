from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Document:
    """A piece of text to be ingested (e.g. the full Markdown of a paper)."""

    id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Chunk:
    """A retrieval unit after chunking; metadata is provided upstream and passed through unchanged."""

    id: str
    document_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
