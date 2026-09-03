from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _client(token: str):
    token = token or os.environ.get("MINERU_TOKEN", "")
    if not token:
        raise RuntimeError("缺少 MINERU_TOKEN：请在 .env 中配置（免费 token 在 https://mineru.net/apiManage/token 获取）")
    try:
        from mineru import MinerU  # mineru-open-sdk
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "缺少 mineru-open-sdk：请先 `pip install mineru-open-sdk` 并设置 MINERU_TOKEN"
        ) from exc
    return MinerU(token)


def extract_pdf(pdf_path: str | Path, *, token: str = "", model: str = "vlm") -> Any:
    """Parse a single PDF and return the full ExtractResult (markdown + images).

    model is ``vlm`` (higher quality for academic papers/formulas/tables) or ``pipeline`` (faster).
    """
    with _client(token) as client:
        return client.extract(str(pdf_path), model=model)


def extract_batch(pdf_paths: list[str | Path], *, token: str = "", model: str = "vlm") -> list[Any]:
    """Parse multiple PDFs and return a list of ExtractResult (in input order)."""
    with _client(token) as client:
        return list(client.extract_batch([str(p) for p in pdf_paths], model=model))
