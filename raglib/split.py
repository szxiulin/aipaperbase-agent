from __future__ import annotations

import re
from typing import Protocol

from .documents import Chunk, Document


_HEADING = re.compile(r"^(#{1,6}\s+.+)$", re.MULTILINE)


class Splitter(Protocol):
    def split(self, document: Document) -> list[Chunk]: ...


class MarkdownSplitter:
    """Split by Markdown headings; oversize sections are further split into paragraphs, with the heading prepended to each chunk as context."""

    def __init__(self, max_chars: int = 2000) -> None:
        self.max_chars = max_chars

    def split(self, document: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        index = 0
        for heading, body in self._sections(document.text):
            for piece in self._pieces(heading, body):
                chunks.append(Chunk(
                    id=f"{document.id}:{index}",
                    document_id=document.id,
                    text=piece,
                    metadata={**document.metadata, "section": heading or ""},
                ))
                index += 1
        return chunks

    @staticmethod
    def _sections(text: str) -> list[tuple[str, str]]:
        matches = list(_HEADING.finditer(text))
        if not matches:
            return [("", text.strip())] if text.strip() else []
        sections: list[tuple[str, str]] = []
        if matches[0].start() > 0:
            preamble = text[: matches[0].start()].strip()
            if preamble:
                sections.append(("", preamble))
        for i, match in enumerate(matches):
            heading = match.group(1).strip()
            body_start = match.end()
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            body = text[body_start:body_end].strip()
            sections.append((heading, body))
        return sections

    def _pieces(self, heading: str, body: str) -> list[str]:
        if not body:
            return [heading] if heading else []
        if not heading and len(body) <= self.max_chars:
            return [body]
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
        pieces: list[str] = []
        current = heading
        for para in paragraphs:
            merged = (current + "\n\n" + para).strip() if current else para
            if len(merged) <= self.max_chars:
                current = merged
            else:
                if current:
                    pieces.append(current)
                current = (heading + "\n\n" + para).strip() if heading else para
        if current:
            pieces.append(current)
        return pieces
