# LLM Inference Gateway

The gateway streams a local model's output through FastAPI. Ollama sends newline-delimited JSON and the gateway translates each frame to a server-sent event. The gateway reports upstream failures and closes upstream connections when streaming ends.

```sh
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:0.5b
curl -N http://localhost:8000/generate -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:0.5b","prompt":"Give one reason to stream inference."}'
python -m unittest -v
```

Run this on localhost. The endpoint is currently unauthenticated.

By default Compose shares a 60-request fixed window across all gateway instances through Redis. Requests beyond the limit receive HTTP 429 with a Retry-After header. Each gateway process admits four active requests and up to eight additional requests, with a five-second wait for an active slot. A full queue, queue timeout, or unavailable Redis returns HTTP 503. An active slot stays occupied until streaming finishes.

Configure these limits with `REQUESTS_PER_MINUTE`, `MAX_ACTIVE`, `MAX_QUEUED`, and `QUEUE_TIMEOUT` in seconds. Running the app without `REDIS_URL` uses only the local concurrency limits.

The gateway waits for the first output before opening the SSE stream. Set `OLLAMA_FALLBACK_URL` to a second Ollama server with the requested model installed. A connection failure, rejected response, invalid stream, or first-output timeout can trigger one fallback attempt. `FIRST_OUTPUT_TIMEOUT` defaults to 10 seconds per attempt and `GENERATION_TIMEOUT` defaults to 60 seconds across attempts and upstream reads after admission. After output starts, upstream errors or deadline expiry produce an SSE error instead of switching models.
