# LLM Inference Gateway

Day 1 streams a local model's output through FastAPI. Ollama sends newline-delimited JSON and the gateway translates each frame to a server-sent event. The gateway reports upstream failures and closes upstream connections when streaming ends.

```sh
docker compose up --build -d
docker compose exec ollama ollama pull qwen2.5:0.5b
curl -N http://localhost:8000/generate -H 'Content-Type: application/json' \
  -d '{"model":"qwen2.5:0.5b","prompt":"Give one reason to stream inference."}'
python -m unittest -v
```

Run this on localhost. The endpoint is currently unauthenticated. Next milestones add admission control, fallback before output, and repeatable performance tests.
