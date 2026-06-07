"""FastAPI bridge for browser-based Voice Agent.

Endpoints:
  GET  /             — serves web/index.html
  GET  /health       — liveness probe
  WS   /voice        — bidirectional audio with the browser

WebSocket protocol (between browser and bridge):
  Browser -> Server:
    - binary frames: PCM16 mono 16 kHz (Web Audio API output)
    - JSON: {"type":"bye"}
  Server -> Browser:
    - JSON: {"type":"status", "stage":"...", "elapsed": float}
    - JSON: {"type":"partial","text":"..."}   (ASR interim)
    - JSON: {"type":"user","text":"..."}      (ASR final sentence)
    - JSON: {"type":"agent_begin"}            (start of an agent reply)
    - JSON: {"type":"agent_chunk","text":"..."}  (one sentence; audio frames
                                                  for THIS sentence follow until
                                                  the next agent_chunk or agent_end)
    - binary frames: PCM16 mono 16 kHz TTS audio
    - JSON: {"type":"agent_end"}
    - JSON: {"type":"bye","reason":"..."}
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import signal
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import wechat_push
from .aliyun_token import token_cache
from .config import settings
from .hermes_client import hermes
from .session_manager import CallSession, sessions
from .stt_service import AsrSession
from .tts_service import synthesize
from .visitor_store import upsert_visitor, sync_hermes_memory, get_visit_count

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("bridge")

app = FastAPI(title="VoiceAgent Bridge")


@app.on_event("startup")
async def _warmup_tts():
    """Pre-synthesize greeting audio and cache NLS token."""
    global _greeting_pcm_cache
    try:
        chunks = []
        async for chunk in synthesize(GREETING):
            chunks.append(chunk)
        _greeting_pcm_cache = b''.join(chunks)
        logger.info("Greeting audio pre-cached: %d bytes (%.1fs)",
                    len(_greeting_pcm_cache), len(_greeting_pcm_cache) / 32000)
    except Exception as e:
        logger.warning("TTS prewarm failed: %s", e)
        _greeting_pcm_cache = b''


_greeting_pcm_cache: bytes = b''
_hermes_gateway_proc: subprocess.Popen | None = None


@app.on_event("startup")
async def _start_hermes_gateway():
    """Start Hermes gateway as a subprocess."""
    global _hermes_gateway_proc
    try:
        _hermes_gateway_proc = subprocess.Popen(
            ["hermes", "-p", "voiceagent", "gateway"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=lambda: None,
        )
        logger.info("Hermes gateway started (pid=%d)", _hermes_gateway_proc.pid)
    except Exception as e:
        logger.warning("Failed to start Hermes gateway: %s", e)


@app.on_event("shutdown")
async def _stop_hermes_gateway():
    """Stop Hermes gateway subprocess."""
    global _hermes_gateway_proc
    if _hermes_gateway_proc and _hermes_gateway_proc.poll() is None:
        _hermes_gateway_proc.terminate()
        try:
            _hermes_gateway_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _hermes_gateway_proc.kill()
        logger.info("Hermes gateway stopped")
    _hermes_gateway_proc = None


sync_hermes_memory()

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "active": sessions.active_count()}


GREETING = "您好，车牌号多少，找哪家公司，什么事？"

# Treat full-width Chinese and half-width punctuation as sentence boundaries.
# Skipped ASCII '.' — too easy to confuse with decimals / abbreviations.
SENTENCE_TERMS = "。！？!?\n"

# JSON output from the visitor-registration skill — may be fenced or bare.
JSON_FENCED_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```")

# A JSON key like `"purpose":` or `"confirmed":` — used to spot stray JSON
# lines that survived sentence-splitting and would otherwise be spoken.
JSON_KEY_RE = re.compile(r'"\w+"\s*:')


def _extract_visitor_json(text: str) -> dict | None:
    """Return the visitor-registration JSON object if present in `text`."""
    for m in JSON_FENCED_RE.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("action") == "register_visitor":
            return obj
    # Fallback: bare {...} containing "action":"register_visitor".
    idx = text.find('"action"')
    if idx >= 0:
        start = text.rfind("{", 0, idx)
        if start >= 0:
            depth = 0
            for j in range(start, len(text)):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : j + 1])
                            if isinstance(obj, dict) and obj.get("action") == "register_visitor":
                                return obj
                        except json.JSONDecodeError:
                            pass
                        break
    return None


def _looks_like_json_fragment(s: str) -> bool:
    """Heuristic: should this sentence be sent to TTS, or is it part of a JSON dump?"""
    s = s.strip()
    if not s:
        return True
    if s.startswith("{") or s.startswith("```") or s.endswith("}") or s.endswith("```"):
        return True
    if JSON_KEY_RE.search(s):
        return True
    return False


async def _send_json(ws: WebSocket, payload: dict) -> None:
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


async def _speak_fixed(ws: WebSocket, session: CallSession, text: str) -> None:
    """Synthesize a known text and stream as a single agent turn (used for greeting)."""
    session.append_agent_turn(text)
    await _send_json(ws, {"type": "status", "stage": "tts", "elapsed": session.elapsed()})
    await _send_json(ws, {"type": "agent_begin"})
    await _send_json(ws, {"type": "agent_chunk", "text": text})
    if text == GREETING and _greeting_pcm_cache:
        # Send 800ms silence + greeting as one chunk to avoid timing gaps
        await ws.send_bytes(b'\x00' * 25600 + _greeting_pcm_cache)
    else:
        await ws.send_bytes(b'\x00' * 25600)
        try:
            async for chunk in synthesize(text):
                await ws.send_bytes(chunk)
        except Exception:
            logger.exception("TTS failed")
    await _send_json(ws, {"type": "agent_end"})


async def _stream_reply(ws: WebSocket, session: CallSession) -> None:
    """Pipe Hermes streaming text into TTS one sentence at a time.

    Two concurrent halves communicate via an asyncio.Queue, so we keep
    reading Hermes deltas while we synthesize the previous sentence.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    full_text = ""

    async def producer() -> None:
        nonlocal full_text
        buf = ""
        try:
            async for delta in hermes.chat_stream(session.transcript):
                full_text += delta
                buf += delta
                while True:
                    cut = -1
                    for ch in SENTENCE_TERMS:
                        i = buf.find(ch)
                        if i >= 0 and (cut == -1 or i < cut):
                            cut = i
                    if cut < 0:
                        break
                    sentence = buf[: cut + 1].strip()
                    buf = buf[cut + 1 :]
                    if sentence and not _looks_like_json_fragment(sentence):
                        await queue.put(sentence)
        except Exception:
            logger.exception("Hermes stream failed")
        finally:
            trailing = buf.strip()
            if trailing and not _looks_like_json_fragment(trailing):
                await queue.put(trailing)
            await queue.put(None)

    async def consumer() -> tuple[bool, int]:
        """Returns (spoke, total_audio_bytes)."""
        spoke = False
        audio_bytes = 0
        while True:
            sentence = await queue.get()
            if sentence is None:
                break
            if not spoke:
                await _send_json(ws, {"type": "status", "stage": "tts", "elapsed": session.elapsed()})
                await _send_json(ws, {"type": "agent_begin"})
                spoke = True
            await _send_json(ws, {"type": "agent_chunk", "text": sentence})
            try:
                async for chunk in synthesize(sentence):
                    await ws.send_bytes(chunk)
                    audio_bytes += len(chunk)
            except Exception:
                logger.exception("TTS sentence failed: %r", sentence)
        return spoke, audio_bytes

    prod_task = asyncio.create_task(producer())
    spoke, audio_bytes = await consumer()
    await prod_task

    visitor = _extract_visitor_json(full_text)

    REQUIRED_FIELDS = ("plate", "company", "phone", "purpose")
    user_turns = sum(1 for m in session.transcript if m.get("role") == "user")
    if (
        visitor
        and visitor.get("confirmed")
        and all(visitor.get(f) for f in REQUIRED_FIELDS)
    ):
        visits = get_visit_count(visitor["plate"])
        if visits >= 3 and user_turns < 2:
            logger.info("Blocking premature registration for returning visitor %s "
                        "(visits=%d, user_turns=%d) — need confirmation first",
                        visitor["plate"], visits, user_turns)
        else:
            session.visitor_info = visitor
            asyncio.create_task(wechat_push.send_visitor(visitor))
            upsert_visitor(visitor)
            session.completed = True
    elif visitor:
        logger.warning("Incomplete visitor JSON, missing: %s",
                       [f for f in REQUIRED_FIELDS if not visitor.get(f)])

    if not spoke:
        fallback = "不好意思，没听清，您再说一遍？"
        await _send_json(ws, {"type": "agent_begin"})
        await _send_json(ws, {"type": "agent_chunk", "text": fallback})
        try:
            async for chunk in synthesize(fallback):
                await ws.send_bytes(chunk)
        except Exception:
            logger.exception("TTS fallback failed")
        full_text = fallback

    session.append_agent_turn(full_text)
    await _send_json(ws, {"type": "agent_end"})

    if session.completed:
        await asyncio.sleep(0.2)
        await _send_json(ws, {"type": "bye", "reason": "registered"})


async def _handle_user_turn(ws: WebSocket, session: CallSession, user_text: str) -> None:
    session.append_user_turn(user_text)
    await _send_json(ws, {"type": "status", "stage": "thinking", "elapsed": session.elapsed()})
    t0 = session.elapsed()
    await _stream_reply(ws, session)
    logger.info("turn latency: %.2fs", session.elapsed() - t0)


async def _prewarm_hermes() -> None:
    """Fire a small request so Anthropic's prompt cache is warm before the
    first real user turn lands."""
    try:
        async for _ in hermes.chat_stream([{"role": "user", "content": "嗨"}]):
            return  # First token is enough; the cache write happens regardless.
    except Exception as e:
        logger.warning("Hermes prewarm failed: %s", e)


@app.websocket("/voice")
async def voice(ws: WebSocket) -> None:
    await ws.accept()
    session = sessions.create()
    logger.info("voice session start: %s from %s", session.session_id, ws.client)

    # Warm Hermes cache in parallel with the greeting playback.
    # Disabled — measurements showed it competes with the real first turn
    # and the cache key (system prompt prefix) is the same with or without it.
    prewarm: asyncio.Task | None = None
    # prewarm = asyncio.create_task(_prewarm_hermes())

    user_turn_queue: asyncio.Queue[str] = asyncio.Queue()

    async def on_partial(text: str) -> None:
        await _send_json(ws, {"type": "partial", "text": text})

    async def on_final(text: str) -> None:
        await _send_json(ws, {"type": "user", "text": text})
        await user_turn_queue.put(text)

    asr_ref: dict[str, AsrSession] = {}

    async def _start_asr() -> AsrSession:
        a = AsrSession(on_partial=on_partial, on_final=on_final)
        await a.start()
        asr_ref["asr"] = a
        return a

    try:
        # Start ASR and send greeting in parallel to reduce startup latency
        asr_task = asyncio.create_task(_start_asr())
        await _speak_fixed(ws, session, GREETING)
        await asr_task
    except Exception:
        logger.exception("ASR start failed")
        await _send_json(ws, {"type": "bye", "reason": "asr_start_failed"})
        await ws.close()
        sessions.drop(session.session_id)
        return

    async def turn_loop() -> None:
        while True:
            text = await user_turn_queue.get()
            if text == "__bye__":
                return
            await _handle_user_turn(ws, session, text)

    turn_task = asyncio.create_task(turn_loop())

    await _send_json(ws, {"type": "status", "stage": "listen", "elapsed": session.elapsed()})

    try:
        while True:
            msg = await ws.receive()
            if msg["type"] == "websocket.disconnect":
                break
            if "bytes" in msg and msg["bytes"]:
                asr = asr_ref.get("asr")
                if asr is None or not asr.alive:
                    # Aliyun NLS will cut the ASR WS after ~10s of idle. Spin up
                    # a fresh session so the user can keep talking without
                    # noticing.
                    try:
                        asr = await _start_asr()
                        logger.info("ASR restarted for session %s", session.session_id)
                    except Exception:
                        logger.exception("ASR restart failed")
                        continue
                await asr.send_pcm(msg["bytes"])
                continue
            if "text" in msg and msg["text"]:
                try:
                    obj = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "bye":
                    logger.info("client bye: %s", session.session_id)
                    break
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("voice handler error")
    finally:
        asr = asr_ref.get("asr")
        if asr is not None:
            await asr.stop()
            try:
                await asyncio.wait_for(asr.closed(), timeout=2)
            except asyncio.TimeoutError:
                pass
        await user_turn_queue.put("__bye__")
        for t in (turn_task, prewarm):
            if t is None:
                continue
            try:
                await asyncio.wait_for(t, timeout=2)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                t.cancel()
        sessions.drop(session.session_id)
        try:
            await ws.close()
        except Exception:
            pass
        logger.info(
            "voice session end: %s elapsed=%.1fs turns=%d",
            session.session_id,
            session.elapsed(),
            len(session.transcript),
        )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "bridge_server.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )
