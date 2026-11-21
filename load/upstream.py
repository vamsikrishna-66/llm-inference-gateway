"""Synthetic Ollama endpoints for repeatable transport failures."""
import asyncio

from fastapi import FastAPI, Response
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post("/{backend}/api/generate")
async def generate(backend: str, body: dict):
    scenario = body["prompt"]
    if scenario == "both-reject" or (scenario == "fallback" and backend == "primary"):
        return Response(status_code=503)

    async def frames():
        yield '{"response":"' + backend + '","done":false}\n'
        if scenario == "stall":
            await asyncio.sleep(10)
        yield '{"done":true}\n'

    return StreamingResponse(frames(), media_type="application/x-ndjson")
