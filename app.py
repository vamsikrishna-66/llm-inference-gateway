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


@app.post("/generate")
async def generate(body: GenerateRequest):
    endpoint = os.getenv("OLLAMA_URL", "http://ollama:11434")
    client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5))
    request = client.build_request(
        "POST", f"{endpoint}/api/generate", json={**body.model_dump(), "stream": True}
    )
    try:
        upstream = await client.send(request, stream=True)
    except httpx.RequestError as error:
        await client.aclose()
        raise HTTPException(502, "Model endpoint unavailable") from error
    if upstream.status_code != 200:
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(502, "Model endpoint rejected the request")

    async def close():
        await upstream.aclose()
        await client.aclose()

    async def events():
        try:
            async for line in upstream.aiter_lines():
                if line:
                    yield f"data: {line}\n\n"
        except httpx.RequestError:
            yield 'event: error\ndata: {"error":"model stream interrupted"}\n\n'
        finally:
            await close()

    return StreamingResponse(
        events(), media_type="text/event-stream", background=BackgroundTask(close)
    )
