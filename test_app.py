import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from app import app


class StreamingTest(unittest.TestCase):
    def test_streams_ollama_frames_as_sse(self):
        def upstream(request):
            self.assertEqual(request.url.path, "/api/generate")
            return httpx.Response(
                200, text='{"response":"hello","done":false}\n{"done":true}\n'
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        with TestClient(app) as gateway, patch(
            "app.httpx.AsyncClient", return_value=upstream_client
        ):
            response = gateway.post("/generate", json={"model": "demo", "prompt": "Hi"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        self.assertEqual(
            response.text, 'data: {"response":"hello","done":false}\n\ndata: {"done":true}\n\n'
        )
        self.assertTrue(upstream_client.is_closed)

    def generate_with_upstreams(self, handler):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with TestClient(app) as gateway, patch("app.httpx.AsyncClient", return_value=client), patch.dict(
            "os.environ", {"OLLAMA_URL": "http://primary", "OLLAMA_FALLBACK_URL": "http://fallback",
                           "FIRST_OUTPUT_TIMEOUT": "0.05", "GENERATION_TIMEOUT": "1", "REDIS_URL": ""}
        ):
            response = gateway.post("/generate", json={"model": "demo", "prompt": "Hi"})
        self.assertTrue(client.is_closed)
        return response

    def test_fallback_after_first_output_timeout(self):
        import asyncio

        hosts = []

        class SlowStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                await asyncio.sleep(10)
                yield b'{"response":"late"}\n'

        def handler(request):
            hosts.append(request.url.host)
            if request.url.host == "primary":
                return httpx.Response(200, stream=SlowStream())
            return httpx.Response(200, text='{"response":"fallback","done":true}\n')

        response = self.generate_with_upstreams(handler)
        self.assertEqual(response.status_code, 200)
        self.assertIn('"fallback"', response.text)
        self.assertEqual(hosts, ["primary", "fallback"])

    def test_incomplete_stream_never_falls_back_after_output(self):
        hosts = []

        def handler(request):
            hosts.append(request.url.host)
            return httpx.Response(200, text='{"response":"partial"}\n')

        response = self.generate_with_upstreams(handler)
        self.assertIn('"partial"', response.text)
        self.assertIn("event: error", response.text)
        self.assertEqual(hosts, ["primary"])
