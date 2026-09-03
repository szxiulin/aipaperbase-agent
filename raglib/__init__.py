"""raglib: a reusable RAG library with zero business-logic dependencies.

It only knows about "documents" and "queries" and is unaware of any upstream business
concepts. All platform/model dependencies are injected through interfaces, so swapping
the implementation (Embedder / Reranker / VectorStore / Generator) requires no changes.
"""

from .documents import Chunk, Document
from .split import MarkdownSplitter, Splitter
from .embed import Embedder, OpenAICompatEmbedder
from .rerank import OpenAICompatReranker, Reranker
from .store import InMemoryStore, QdrantStore, VectorStore
from .retrieve import Retriever
from .generate import Generator, OpenAICompatGenerator
from .pipeline import Pipeline
from .config import RAGConfig, load_config, load_dotenv

__all__ = [
    "Chunk",
    "Document",
    "Splitter",
    "MarkdownSplitter",
    "Embedder",
    "OpenAICompatEmbedder",
    "Reranker",
    "OpenAICompatReranker",
    "VectorStore",
    "InMemoryStore",
    "QdrantStore",
    "Retriever",
    "Generator",
    "OpenAICompatGenerator",
    "Pipeline",
    "RAGConfig",
    "load_config",
    "load_dotenv",
]
