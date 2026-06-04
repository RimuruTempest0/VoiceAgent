"""Aliyun NLS streaming speech recognition (real-time ASR).

Protocol:
  wss://nls-gateway-cn-shanghai.aliyuncs.com/ws/v1
  Auth via ?token=<NLS token>
  Frames:
    1. Send JSON header: StartTranscription
    2. Send binary PCM16 (16 kHz, mono) chunks
    3. Receive JSON events: SentenceBegin / TranscriptionResultChanged / SentenceEnd
    4. Send JSON header: StopTranscription
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import AsyncIterator, Awaitable, Callable

import websockets

from .aliyun_token import token_cache
from .config import settings

logger = logging.getLogger(__name__)

NLS_WS_URL = "wss://nls-gateway-{region}.aliyuncs.com/ws/v1"


def _header(name: str, task_id: str, message_id: str | None = None) -> dict:
    return {
        "appkey": settings.aliyun_nls_appkey,
        "message_id": message_id or uuid.uuid4().hex,
        "task_id": task_id,
        "namespace": "SpeechTranscriber",
        "name": name,
    }


class AsrSession:
    """One streaming ASR session over a single WebSocket.

    Usage:
        asr = AsrSession(on_partial=..., on_final=...)
        await asr.start()
        await asr.send_pcm(pcm_bytes)        # repeat
        await asr.stop()
        await asr.closed()                   # awaits TranscriptionCompleted
    """

    def __init__(
        self,
        on_partial: Callable[[str], Awaitable[None]] | None = None,
        on_final: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self._task_id = uuid.uuid4().hex
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._reader: asyncio.Task | None = None
        self._closed = asyncio.Event()
        self._started = asyncio.Event()
        self.on_partial = on_partial or (lambda text: asyncio.sleep(0))
        self.on_final = on_final or (lambda text: asyncio.sleep(0))

    async def start(self) -> None:
        token = await token_cache.get()
        url = NLS_WS_URL.format(region=settings.aliyun_nls_region) + f"?token={token}"
        self._ws = await websockets.connect(url, max_size=2**22, open_timeout=5)
        self._reader = asyncio.create_task(self._read_loop())

        start_msg = {
            "header": _header("StartTranscription", self._task_id),
            "payload": {
                "format": "pcm",
                "sample_rate": 16000,
                "enable_intermediate_result": True,
                "enable_punctuation_prediction": True,
                "enable_inverse_text_normalization": True,
                "max_sentence_silence": 600,  # ms; tighter = snappier turn-taking
            },
        }
        await self._ws.send(json.dumps(start_msg))
        # Wait for TranscriptionStarted before allowing audio.
        try:
            await asyncio.wait_for(self._started.wait(), timeout=5)
        except asyncio.TimeoutError as e:
            raise RuntimeError("NLS ASR did not return TranscriptionStarted") from e

    async def send_pcm(self, pcm: bytes) -> None:
        if not self._ws or not self._started.is_set() or self._closed.is_set():
            return
        try:
            await self._ws.send(pcm)
        except websockets.ConnectionClosed:
            logger.warning("ASR ws closed mid-stream; will need restart")
            self._closed.set()

    @property
    def alive(self) -> bool:
        return self._started.is_set() and not self._closed.is_set()

    async def stop(self) -> None:
        if not self._ws:
            return
        stop_msg = {"header": _header("StopTranscription", self._task_id)}
        try:
            await self._ws.send(json.dumps(stop_msg))
        except websockets.ConnectionClosed:
            pass

    async def closed(self) -> None:
        await self._closed.wait()

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                msg = json.loads(raw)
                name = msg.get("header", {}).get("name")
                payload = msg.get("payload", {})
                text = payload.get("result", "")
                if name == "TranscriptionStarted":
                    self._started.set()
                elif name == "TranscriptionResultChanged":
                    if text:
                        await self.on_partial(text)
                elif name == "SentenceEnd":
                    if text:
                        await self.on_final(text)
                elif name == "TranscriptionCompleted":
                    break
                elif name == "TaskFailed":
                    logger.warning("NLS ASR TaskFailed: %s", msg.get("header"))
                    break
        except websockets.ConnectionClosed:
            pass
        except Exception:
            logger.exception("ASR read loop crashed")
        finally:
            self._closed.set()
            if self._ws:
                try:
                    await self._ws.close()
                except Exception:
                    pass
