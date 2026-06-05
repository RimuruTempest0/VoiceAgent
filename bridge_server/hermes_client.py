"""Hermes Agent client (OpenAI-compatible).

Hermes exposes /v1/chat/completions at HERMES_BASE_URL. We inject the
visitor-registration SKILL.md as a system-role message at the head of every
request — Hermes loads its own 14k system prompt before our call, and we
observed that prompt was overriding the per-skill constraints. Putting the
skill as an explicit system role brings DeepSeek (or whichever underlying
model Hermes routes to) back in line.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import AsyncIterator

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _load_skill_system_prompt() -> str:
    p = Path(settings.skill_path)
    if not p.exists():
        logger.warning("SKILL.md not found at %s — running without injected system prompt", p)
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        logger.exception("failed to read SKILL.md")
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            text = text[end + 4 :].lstrip()
    return text


def _load_visitor_memory() -> str:
    """Load USER.md so the model can recognize return visitors."""
    p = Path(settings.skill_path).parent.parent.parent / "memories" / "USER.md"
    if not p.exists():
        return ""
    try:
        text = p.read_text(encoding="utf-8")
    except Exception:
        return ""
    # Strip the Hermes-internal header (everything before §)
    if "§" in text:
        text = text.split("§", 1)[1].strip()
    return text


class HermesClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.hermes_base_url).rstrip("/")
        self.api_key = api_key or settings.hermes_api_key
        self.model = model or settings.hermes_model
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=5.0),
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        self.system_prompt = _load_skill_system_prompt()
        logger.info("Hermes system prompt loaded: %d chars", len(self.system_prompt))

    def _wrap(self, messages: list[dict]) -> list[dict]:
        if not self.system_prompt:
            return messages
        if messages and messages[0].get("role") == "system":
            return messages
        memory = _load_visitor_memory()
        content = self.system_prompt
        if memory:
            content += "\n\n### 已知访客记录（来自数据库）\n" + memory
        return [{"role": "system", "content": content}, *messages]

    async def chat(self, messages: list[dict]) -> str:
        payload = {"model": self.model, "messages": self._wrap(messages)}
        resp = await self._client.post(
            f"{self.base_url}/chat/completions", json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def chat_stream(self, messages: list[dict]) -> AsyncIterator[str]:
        """Stream incremental content deltas. Use when latency matters."""
        import json
        payload = {"model": self.model, "messages": self._wrap(messages), "stream": True}
        async with self._client.stream(
            "POST", f"{self.base_url}/chat/completions", json=payload
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    continue
                choices = chunk.get("choices") or []
                if not choices:
                    # Some servers send a trailing usage-only event.
                    continue
                delta = choices[0].get("delta", {}).get("content")
                if delta:
                    yield delta

    async def close(self) -> None:
        await self._client.aclose()


hermes = HermesClient()
