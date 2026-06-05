"""Multi-turn end-to-end: streams user audio, replies with "对/没错" until
the agent completes registration (bye reason=registered) or times out.

Verifies the visitor-registration JSON detection + WeChat push path.
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
CONFIRM = "对，没错。"

MAX_TURNS = 6
TURN_TIMEOUT = 35


async def synth(text: str) -> bytes:
    pcm = bytearray()
    async for c in synthesize(text):
        pcm.extend(c)
    return bytes(pcm)


async def background_silence(ws, stop_event: asyncio.Event):
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


async def stream_audio(ws, pcm: bytes):
    chunk = 640 * 2
    for i in range(0, len(pcm), chunk):
        try:
            await ws.send(pcm[i : i + chunk])
        except Exception:
            return
        await asyncio.sleep(0.02)


async def main(port: int = 8000) -> None:
    print("== synthesizing user utterances")
    pcm1, pcm_confirm = await asyncio.gather(synth(TURN1), synth(CONFIRM))
    print(f"   turn1={len(pcm1)/2/16000:.2f}s, confirm={len(pcm_confirm)/2/16000:.2f}s")

    uri = f"ws://127.0.0.1:{port}/voice"
    print(f"== connecting {uri}")

    turns_played = 0
    registered = False
    chunks: list[tuple[float, str]] = []
    agent_end_event = asyncio.Event()
    done_event = asyncio.Event()

    async with websockets.connect(uri, max_size=2**22) as ws:
        t0 = time.monotonic()

        async def reader():
            nonlocal registered
            try:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        continue
                    data = json.loads(msg)
                    t = data.get("type")
                    ts = time.monotonic() - t0
                    if t == "agent_chunk":
                        chunks.append((ts, data["text"]))
                        print(f"   [agent @{ts:5.2f}s] {data['text']}")
                    elif t == "agent_end":
                        print(f"   ↳ agent_end @{ts:.2f}s")
                        agent_end_event.set()
                    elif t == "user":
                        print(f"   [user @{ts:.2f}s] {data['text']}")
                    elif t == "status":
                        print(f"   [status @{ts:.2f}s] {data['stage']}")
                    elif t == "bye":
                        print(f"   [bye @{ts:.2f}s] reason={data.get('reason')}")
                        registered = data.get("reason") == "registered"
                        done_event.set()
                        return
            except websockets.ConnectionClosed:
                done_event.set()

        reader_task = asyncio.create_task(reader())
        stop_silence = asyncio.Event()
        silence_task = asyncio.create_task(background_silence(ws, stop_silence))

        # Wait for greeting
        await agent_end_event.wait()
        agent_end_event.clear()
        print("== greeting done, starting conversation")

        # Turn loop: send info first, then confirm until registered or max
        while turns_played < MAX_TURNS and not done_event.is_set():
            pcm = pcm1 if turns_played == 0 else pcm_confirm
            label = "info" if turns_played == 0 else f"confirm#{turns_played}"
            print(f"== user turn ({label})")

            stop_silence.set()
            await silence_task
            await stream_audio(ws, pcm)
            stop_silence.clear()
            silence_task = asyncio.create_task(background_silence(ws, stop_silence))

            turns_played += 1

            # Wait for either agent reply or registration bye
            agent_end_event.clear()
            wait_end = asyncio.create_task(agent_end_event.wait())
            wait_done = asyncio.create_task(done_event.wait())
            try:
                done, pending = await asyncio.wait(
                    {wait_end, wait_done},
                    timeout=TURN_TIMEOUT,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for p in pending:
                    p.cancel()
                if not done:
                    print(f"!! timeout waiting for agent reply (turn {turns_played})")
                    break
            except asyncio.TimeoutError:
                print(f"!! timeout waiting for agent reply (turn {turns_played})")
                break

            if done_event.is_set():
                break

        # Cleanup
        stop_silence.set()
        try:
            await asyncio.wait_for(silence_task, timeout=1)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            silence_task.cancel()

        if not done_event.is_set():
            try:
                await asyncio.wait_for(done_event.wait(), timeout=10)
            except asyncio.TimeoutError:
                await ws.send(json.dumps({"type": "bye"}))
                await asyncio.wait_for(reader_task, timeout=3)

    elapsed = time.monotonic() - t0
    print()
    print(f"== RESULT: registered={registered}, turns={turns_played}, "
          f"elapsed={elapsed:.1f}s, chunks={len(chunks)}")
    if not registered:
        print("!! FAIL — did not receive bye reason=registered")
        sys.exit(1)
    print("== PASS")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
