from __future__ import annotations

from typing import Any, Protocol

from ._budget import charge
from .documents import Chunk


class Generator(Protocol):
    def generate(self, query: str, chunks: list[Chunk], history: list[dict] | None = None) -> dict:
        """Return {"answer", "finish_reason", "reasoning", "model"}."""


class OpenAICompatGenerator:
    """OpenAI-compatible chat/completions endpoint that generates cited answers from retrieved chunks.

    generate() return structure:
      - answer: the generated answer text (may be empty; check finish_reason)
      - finish_reason: "stop"=natural end; "length"=truncated; "content_filter"=refused
      - reasoning: the reasoning from thinking mode (empty when thinking=disabled)
      - model: the model name actually used
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        thinking: str = "enabled",
        reasoning_effort: str = "high",
        temperature: float = 0.7,
        top_p: float = 1.0,
        max_tokens: int = 2048,
    ) -> None:
        import httpx

        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.thinking = thinking
        self.reasoning_effort = reasoning_effort
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self._client = httpx.Client(timeout=120)

    def generate(self, query: str, chunks: list[Chunk], history: list[dict] | None = None) -> dict[str, Any]:
        context = "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))
        prompt = (
            "你是学术论文助手。请根据下面提供的论文片段回答用户问题，"
            "并在引用处标注片段编号 [n]；若片段不足以回答，请明确说明。\n\n"
            f"论文片段：\n{context}\n\n用户问题：{query}"
        )
        messages: list[dict] = []
        for turn in (history or [])[-4:]:
            role = turn.get("role") if turn.get("role") in ("user", "assistant") else "user"
            content = (turn.get("content") or "").strip()
            if content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": prompt})
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if self.thinking == "enabled":
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = self.reasoning_effort
        else:
            payload["thinking"] = {"type": "disabled"}
            payload["temperature"] = self.temperature
            payload["top_p"] = self.top_p
        response = self._client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") or {}
        charge(usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {
            "answer": message.get("content") or "",
            "finish_reason": choice.get("finish_reason") or "stop",
            "reasoning": message.get("reasoning_content") or "",
            "model": data.get("model") or self.model,
        }
