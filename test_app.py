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
