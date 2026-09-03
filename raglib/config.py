from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


@dataclass
class EmbedderConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dim: int = 0


@dataclass
class RerankerConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass
class GeneratorConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    thinking: str = "enabled"        # enabled / disabled
    reasoning_effort: str = "high"   # low / high / max (only applies when thinking mode is enabled)
    temperature: float = 0.7         # only applies when thinking mode is disabled
    top_p: float = 1.0               # only applies when thinking mode is disabled
    max_tokens: int = 2048


@dataclass
class QdrantConfig:
    url: str = ""
    collection: str = "papers"


@dataclass
class RAGConfig:
    embedder: EmbedderConfig
    reranker: RerankerConfig
    generator: GeneratorConfig
    qdrant: QdrantConfig


def load_dotenv(path: str = ".env") -> None:
    """Minimal .env loader: one KEY=VALUE per line; existing environment variables are not overwritten."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def load_config(env: Mapping[str, str] | None = None) -> RAGConfig:
    env = env or os.environ
    return RAGConfig(
        embedder=EmbedderConfig(
            base_url=env.get("EMBEDDING_BASE_URL", ""),
            api_key=env.get("EMBEDDING_API_KEY", ""),
            model=env.get("EMBEDDING_MODEL", ""),
            dim=int(env.get("EMBEDDING_DIM", "0") or 0),
        ),
        reranker=RerankerConfig(
            base_url=env.get("RERANKER_BASE_URL", ""),
            api_key=env.get("RERANKER_API_KEY", ""),
            model=env.get("RERANKER_MODEL", ""),
        ),
        generator=GeneratorConfig(
            base_url=env.get("GENERATOR_BASE_URL", ""),
            api_key=env.get("GENERATOR_API_KEY", ""),
            model=env.get("GENERATOR_MODEL", ""),
            thinking=env.get("GENERATOR_THINKING", "enabled"),
            reasoning_effort=env.get("GENERATOR_REASONING_EFFORT", "high"),
            temperature=float(env.get("GENERATOR_TEMPERATURE", "0.7") or 0.7),
            top_p=float(env.get("GENERATOR_TOP_P", "1.0") or 1.0),
            max_tokens=int(env.get("GENERATOR_MAX_TOKENS", "2048") or 2048),
        ),
        qdrant=QdrantConfig(
            url=env.get("QDRANT_URL", ""),
            collection=env.get("QDRANT_COLLECTION", "papers"),
        ),
    )
