import asyncio
import json
import os

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from admission import Admission

app = FastAPI()
app.add_middleware(Admission)


class GenerateRequest(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    prompt: str = Field(min_length=1, max_length=16_000)


async def frames(upstream):
    async for line in upstream.aiter_lines():
        if not line:
            continue
        try:
            frame = json.loads(line)
        except ValueError as error:
            raise ValueError("Invalid Ollama frame") from error
        if not isinstance(frame, dict) or "error" in frame:
            raise ValueError("Ollama reported a stream error")
        if frame.get("response") or frame.get("done") is True:
            yield line, frame.get("done") is True
        if frame.get("done") is True:
            return
    raise ValueError("Ollama stream ended without a completion frame")


@app.post("/generate")
async def generate(body: GenerateRequest):
    primary = os.getenv("OLLAMA_URL", "http://ollama:11434")
    fallback = os.getenv("OLLAMA_FALLBACK_URL")
    endpoints = list(dict.fromkeys(filter(None, [primary, fallback])))
    loop = asyncio.get_running_loop()
    deadline = loop.time() + float(os.getenv("GENERATION_TIMEOUT", "60"))
    first_output_timeout = float(os.getenv("FIRST_OUTPUT_TIMEOUT", "10"))
    client = httpx.AsyncClient(timeout=None)
    timed_out = False

    try:
        for endpoint in endpoints:
            upstream = None
            try:
                async with asyncio.timeout_at(min(deadline, loop.time() + first_output_timeout)):
                    request = client.build_request(
                        "POST", f"{endpoint.rstrip('/')}/api/generate",
                        json={**body.model_dump(), "stream": True},
                    )
                    upstream = await client.send(request, stream=True)
                    upstream.raise_for_status()
                    stream = frames(upstream)
                    first, done = await anext(stream)
                break
            except (httpx.HTTPError, ValueError, TimeoutError) as error:
                timed_out = isinstance(error, TimeoutError)
                if upstream is not None:
                    await upstream.aclose()
                if loop.time() >= deadline:
                    raise HTTPException(504, "Model deadline exceeded") from error
            except BaseException:
                if upstream is not None:
                    await upstream.aclose()
                raise
        else:
            raise HTTPException(
                504 if timed_out else 502,
                "Model deadline exceeded" if timed_out else "Model endpoint unavailable",
            )
    except BaseException:
        await client.aclose()
        raise

    async def close():
        try:
            await upstream.aclose()
        finally:
            await client.aclose()

    async def events():
        try:
            yield f"data: {first}\n\n"
            if not done:
                while True:
                    async with asyncio.timeout_at(deadline):
                        line, finished = await anext(stream)
                    yield f"data: {line}\n\n"
                    if finished:
                        return
        except TimeoutError:
            yield 'event: error\ndata: {"error":"model deadline exceeded"}\n\n'
        except (httpx.HTTPError, ValueError):
            yield 'event: error\ndata: {"error":"model stream interrupted"}\n\n'
        finally:
            await close()

    return StreamingResponse(
        events(), media_type="text/event-stream", background=BackgroundTask(close)
    )
