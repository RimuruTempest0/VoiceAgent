"""Aliyun NLS streaming text-to-speech.

Protocol mirrors the ASR side: connect, send StartSynthesis, receive
binary PCM16 chunks plus JSON status events, terminate on
SynthesisCompleted.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator

import websockets

from .aliyun_token import token_cache
from .config import settings

logger = logging.getLogger(__name__)

NLS_WS_URL = "wss://nls-gateway-{region}.aliyuncs.com/ws/v1"


def _header(name: str, task_id: str) -> dict:
    return {
        "appkey": settings.aliyun_nls_appkey,
        "message_id": uuid.uuid4().hex,
        "task_id": task_id,
        "namespace": "SpeechSynthesizer",
        "name": name,
    }


async def synthesize(
    text: str,
    *,
    voice: str | None = None,
    sample_rate: int = 16000,
) -> AsyncIterator[bytes]:
    """Yield PCM16 mono chunks for `text`.

    The caller is expected to forward chunks to the browser as they arrive
    so first-byte latency stays low.
    """
    token = await token_cache.get()
    url = NLS_WS_URL.format(region=settings.aliyun_nls_region) + f"?token={token}"
    task_id = uuid.uuid4().hex

    start_msg = {
        "header": _header("StartSynthesis", task_id),
        "payload": {
            "text": text,
            "voice": voice or settings.aliyun_nls_tts_voice,
            "format": "pcm",
            "sample_rate": sample_rate,
            "volume": 60,
            "speech_rate": 80,
            "pitch_rate": 0,
        },
    }

    async with websockets.connect(url, max_size=2**22, open_timeout=5) as ws:
        await ws.send(json.dumps(start_msg))
        async for frame in ws:
            if isinstance(frame, bytes):
                yield frame
                continue
            msg = json.loads(frame)
            name = msg.get("header", {}).get("name")
            if name == "SynthesisCompleted":
                return
            if name == "TaskFailed":
                logger.warning("NLS TTS TaskFailed: %s", msg.get("header"))
                return
