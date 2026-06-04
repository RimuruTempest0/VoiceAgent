"""Headless end-to-end driver for /voice.

Simulates a browser: connects to the WS, TTS-synthesizes a fake user
utterance, streams it into the bridge as PCM16/16k, then collects the
agent's reply (text + audio bytes) and reports timing.

Run while uvicorn is up:
    .venv/bin/python -m uvicorn bridge_server.main:app --port 8000 &
    .venv/bin/python bridge_server/tests/e2e_voice.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import time

import websockets

sys.path.insert(0, "/home/rimuru/VoiceAgent")
from bridge_server.tts_service import synthesize  # noqa: E402

USER_LINE = "您好我的车牌沪A12345来蓝色鲸鱼科技送货手机号13812345678"


async def main(port: int = 8000) -> None:
    print(f"== synthesizing user utterance ({USER_LINE!r})")
    pcm = bytearray()
    async for chunk in synthesize(USER_LINE):
        pcm.extend(chunk)
    print(f"   {len(pcm)} bytes ({len(pcm)/2/16000:.2f}s of audio)")

    uri = f"ws://127.0.0.1:{port}/voice"
    print(f"== connecting {uri}")
    async with websockets.connect(uri, max_size=2**22) as ws:
        t0 = time.monotonic()

        greeting_audio_bytes = 0
        greeting_text = ""
        agent_text = ""
        agent_audio_bytes = 0
        user_final = ""
        greet_end_seen = False
        first_agent_audio = None
        user_final_at = None

        async def receiver():
            nonlocal greeting_audio_bytes, greeting_text, agent_text
            nonlocal agent_audio_bytes, user_final, greet_end_seen, first_agent_audio
            nonlocal user_final_at
            try:
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, bytes):
                        if not greet_end_seen:
                            greeting_audio_bytes += len(msg)
                        else:
                            if first_agent_audio is None:
                                first_agent_audio = time.monotonic() - t0
                                if user_final_at is not None:
                                    print(f"   [first audio] +{first_agent_audio:.2f}s  (Δfrom user final = {first_agent_audio-user_final_at:.2f}s)")
                            agent_audio_bytes += len(msg)
                        continue
                    data = json.loads(msg)
                    t = data.get("type")
                    if t == "agent_begin":
                        pass
                    elif t == "agent_chunk":
                        if not greet_end_seen:
                            greeting_text += data["text"]
                            print(f"   [greet chunk] {data['text']}")
                        else:
                            agent_text += data["text"]
                            print(f"   [agent chunk] +{time.monotonic()-t0:.2f}s  {data['text']!r}")
                    elif t == "agent_end":
                        if not greet_end_seen:
                            greet_end_seen = True
                            print(f"   [greet_end] @ {time.monotonic()-t0:.2f}s, greeting audio={greeting_audio_bytes}B")
                        else:
                            print(f"   [agent_end] @ {time.monotonic()-t0:.2f}s, agent audio={agent_audio_bytes}B")
                            break
                    elif t == "partial":
                        pass
                    elif t == "user":
                        user_final = data["text"]
                        user_final_at = time.monotonic() - t0
                        print(f"   [user final] @ {user_final_at:.2f}s  {user_final}")
                    elif t == "status":
                        print(f"   [status]  {data['stage']}  @{data.get('elapsed',0):.2f}s")
                    elif t == "bye":
                        break
            except websockets.ConnectionClosed:
                pass

        async def sender():
            while not greet_end_seen:
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.3)
            print("== streaming user audio")
            chunk = 640 * 2
            for i in range(0, len(pcm), chunk):
                await ws.send(bytes(pcm[i:i+chunk]))
                await asyncio.sleep(0.02)
            # Mimic the browser: keep streaming silence so NLS detects sentence end
            # AND so the ASR WS doesn't trip its IDLE_TIMEOUT while we wait for
            # the agent reply. 20 ms per chunk.
            silence = b"\x00" * chunk
            for _ in range(30 * 50):  # up to 30s
                await ws.send(silence)
                await asyncio.sleep(0.02)
                if agent_text and first_agent_audio is not None and agent_audio_bytes > 200_000:
                    await asyncio.sleep(0.3)
                    break
            await ws.send(json.dumps({"type": "bye"}))

        await asyncio.wait_for(asyncio.gather(receiver(), sender()), timeout=60)

    print(f"\n== summary")
    print(f"   user transcript:  {user_final}")
    print(f"   agent reply:      {agent_text}")
    print(f"   first agent audio: {first_agent_audio and f'{first_agent_audio:.2f}s' or 'n/a'}")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 8000))
