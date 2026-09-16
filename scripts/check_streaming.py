"""Exercise ticket creation and real WebSocket transport locally or on GreenNode.

Default: send silence (no inference). --wav: send a PCM16 mono 16 kHz WAV at
microphone speed, printing event timing/text. Reads SERVICE_API_KEY from the
environment, never from command-line arguments or logs.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import os
import time
from urllib.parse import urlsplit, urlunsplit
import wave

import httpx
from websockets.asyncio.client import connect


async def check(base: str, origin: str, wav_path: str | None):
    pcm = bytes(16000)  # 500 ms silence: verifies transport without inference
    if wav_path:
        with wave.open(wav_path, "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
                raise ValueError("WAV must be PCM16 mono at 16 kHz")
            pcm = wav.readframes(wav.getnframes())
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(base.rstrip("/") + "/api/v1/stream-sessions",
                                     json={"origin": origin},
                                     headers={"x-api-key": os.environ.get("SERVICE_API_KEY", "")})
        if response.status_code != 200:
            raise RuntimeError(f"Ticket endpoint returned HTTP {response.status_code}; check image/configuration")
        token = response.json()["token"]
    parsed = urlsplit(base)
    url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc,
                      "/api/v1/transcriptions/stream", "", ""))
    started = time.monotonic()
    async with connect(url, origin=origin, open_timeout=10, max_size=65536) as ws:
        await ws.send(json.dumps({"type": "start", "token": token}))
        ready = json.loads(await asyncio.wait_for(ws.recv(), 10))
        if ready.get("type") != "ready":
            raise RuntimeError(f"WebSocket rejected session: {ready.get('code', 'invalid ready')}")
        print("ready (WebSocket Upgrade succeeded)", flush=True)

        async def send():
            for offset in range(0, len(pcm), 3200):
                await ws.send(pcm[offset:offset + 3200])
                await asyncio.sleep(0.1)
            await ws.send(json.dumps({"type": "stop"}))

        sender = asyncio.create_task(send())
        try:
            while True:
                message = json.loads(await asyncio.wait_for(ws.recv(), 130))
                print(f"{time.monotonic() - started:.2f}s {json.dumps(message, ensure_ascii=False)}", flush=True)
                if message["type"] == "finishing":
                    sender.cancel()
                if message["type"] == "error":
                    raise RuntimeError("Streaming failed")
                if message["type"] == "final":
                    break
        finally:
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--origin", default="http://localhost:3000")
    parser.add_argument("--wav")
    args = parser.parse_args()
    asyncio.run(check(args.base_url, args.origin, args.wav))
