"""Two-turn end-to-end: user info → Hermes confirm → user 对 → Hermes JSON → bridge push.

Verifies the visitor-registration JSON detection + (mocked) WeChat push path.
Run while uvicorn is up.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets

sys.path.insert(0, "/home/rimuru/VoiceAgent")
from bridge_server.tts_service import synthesize  # noqa: E402

TURN1 = "您好，我的车牌沪A12345，来蓝色鲸鱼科技送货，手机号13812345678。"
TURN2 = "对，没错。"


async def synth(text: str) -> bytes:
    pcm = bytearray()
    async for c in synthesize(text):
        pcm.extend(c)
    return bytes(pcm)


async def background_silence(ws, stop_event: asyncio.Event):
    """Mimic a real browser: keep sending 20 ms mic frames at all times."""
    chunk = 640 * 2
    silence = b"\x00" * chunk
    try:
        while not stop_event.is_set():
            try:
                await ws.send(silence)
            except Exception:
                return
            await asyncio.sleep(0.02)
    except asyncio.CancelledError:
        pass


async def stream_audio(ws, pcm: bytes, silence_task: asyncio.Task | None):
    # Pause background silence while real audio plays, then resume.
    chunk = 640 * 2
    for i in range(0, len(pcm), chunk):
        try:
            await ws.send(pcm[i:i+chunk])
        except Exception:
            return
        await asyncio.sleep(0.02)


async def main(port: int = 8000) -> None:
    print(f"== synthesizing 2 user utterances")
    pcm1, pcm2 = await asyncio.gather(synth(TURN1), synth(TURN2))
    print(f"   turn1={len(pcm1)/2/16000:.2f}s, turn2={len(pcm2)/2/16000:.2f}s")

    uri = f"ws://127.0.0.1:{port}/voice"
    print(f"== connecting {uri}")
    state = {
        "greet_end": False,
        "first_agent_end": False,
        "second_agent_end": False,
        "registered_bye": False,
        "agent_chunks": [],
    }
    agent_end_event = asyncio.Event()

    async with websockets.connect(uri, max_size=2**22) as ws:
        t0 = time.monotonic()

        async def reader():
            try:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        continue
                    data = json.loads(msg)
                    t = data.get("type")
                    if t == "agent_chunk":
                        ts = time.monotonic() - t0
                        state["agent_chunks"].append((ts, data["text"]))
                        marker = "greet" if not state["greet_end"] else ("turn1" if not state["first_agent_end"] else "turn2")
                        print(f"   [{marker} chunk @{ts:5.2f}s] {data['text']}")
                    elif t == "agent_end":
                        if not state["greet_end"]:
                            state["greet_end"] = True
                            print(f"   ↳ greet_end @{time.monotonic()-t0:.2f}s")
                            agent_end_event.set()
                        elif not state["first_agent_end"]:
                            state["first_agent_end"] = True
                            print(f"   ↳ turn1_end @{time.monotonic()-t0:.2f}s")
                            agent_end_event.set()
                        else:
                            state["second_agent_end"] = True
                            print(f"   ↳ turn2_end @{time.monotonic()-t0:.2f}s")
                            agent_end_event.set()
                    elif t == "user":
                        print(f"   [user @{time.monotonic()-t0:.2f}s] {data['text']}")
                    elif t == "status":
                        print(f"   [status @{time.monotonic()-t0:.2f}s] {data['stage']}")
                    elif t == "bye":
                        print(f"   [bye @{time.monotonic()-t0:.2f}s] reason={data.get('reason')}")
                        state["registered_bye"] = data.get("reason") == "registered"
                        return
            except websockets.ConnectionClosed:
                pass

        reader_task = asyncio.create_task(reader())
        stop_silence = asyncio.Event()
        silence_task = asyncio.create_task(background_silence(ws, stop_silence))

        # 1) Wait for greeting to finish
        await agent_end_event.wait()
        agent_end_event.clear()

        # 2) Stream user turn 1
        print("== user turn 1")
        stop_silence.set()
        await silence_task
        await stream_audio(ws, pcm1, None)
        stop_silence.clear()
        silence_task = asyncio.create_task(background_silence(ws, stop_silence))

        # 3) Wait for agent reply to finish
        await agent_end_event.wait()
        agent_end_event.clear()

        # 4) Stream user turn 2 ("对，没错")
        print("== user turn 2")
        stop_silence.set()
        await silence_task
        await stream_audio(ws, pcm2, None)
        stop_silence.clear()
        silence_task = asyncio.create_task(background_silence(ws, stop_silence))

        # 5) Wait for agent reply (should include JSON detection + push + confirm)
        try:
            await asyncio.wait_for(agent_end_event.wait(), timeout=30)
        except asyncio.TimeoutError:
            print("!! timeout waiting for turn2 agent_end")

        stop_silence.set()
        try:
            await asyncio.wait_for(silence_task, timeout=1)
        except asyncio.TimeoutError:
            silence_task.cancel()

        # 6) Wait for server bye or close
        try:
            await asyncio.wait_for(reader_task, timeout=5)
        except asyncio.TimeoutError:
            await ws.send(json.dumps({"type": "bye"}))

    print()
    print("== state:", {k: v for k, v in state.items() if k != "agent_chunks"})
    print(f"== {len(state['agent_chunks'])} chunks total")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
