"""Start a local gateway and synthetic upstreams, then measure failure scenarios."""
import argparse
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import httpx

GATEWAY = "http://127.0.0.1:8000"
UPSTREAM = "http://127.0.0.1:11435"
CASES = {
    "fallback": (200, ('"response":"fallback"', '"done":true'), ("event: error",)),
    "both-reject": (502, ("Model endpoint unavailable",), ()),
    "stall": (200, ('"response":"primary"', "event: error", "model deadline exceeded"),
              ('"done":true', '"response":"fallback"')),
}


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def start(stack, module, port, url, env):
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module, "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        env=env,
    )
    stack.callback(stop, process)
    with httpx.Client(trust_env=False, timeout=0.5) as client:
        for _ in range(80):
            if process.poll() is not None:
                raise RuntimeError(f"{module} exited before startup")
            try:
                client.get(f"{url}/openapi.json").raise_for_status()
            except httpx.RequestError:
                time.sleep(0.05)
            else:
                return
    raise RuntimeError(f"{module} did not become ready")


def measure(client, scenario):
    status, required, forbidden = CASES[scenario]
    start_time = time.perf_counter()
    response = client.post(f"{GATEWAY}/generate", json={"model": "synthetic", "prompt": scenario})
    elapsed = (time.perf_counter() - start_time) * 1000
    ok = (response.status_code == status and all(item in response.text for item in required)
          and not any(item in response.text for item in forbidden))
    return elapsed, ok


def run(use_k6):
    env = {key: value for key, value in os.environ.items()
           if key.lower() not in {"http_proxy", "https_proxy", "all_proxy"}}
    env.update(
        REDIS_URL="", MAX_ACTIVE="6", FIRST_OUTPUT_TIMEOUT="0.5", GENERATION_TIMEOUT="1",
        OLLAMA_URL=f"{UPSTREAM}/primary", OLLAMA_FALLBACK_URL=f"{UPSTREAM}/fallback",
    )
    with ExitStack() as stack:
        start(stack, "load.upstream:app", 11435, UPSTREAM, env)
        start(stack, "app:app", 8000, GATEWAY, env)
        if use_k6:
            return subprocess.run(["k6", "run", "load/failures.js"], env=env).returncode

        with httpx.Client(trust_env=False, timeout=5) as client, ThreadPoolExecutor(6) as workers:
            failed = 0
            for scenario in CASES:
                samples = list(workers.map(lambda _: measure(client, scenario), range(36)))
                latencies = sorted(elapsed for elapsed, _ in samples)
                p95 = latencies[math.ceil(0.95 * len(latencies)) - 1]
                errors = sum(not ok for _, ok in samples)
                failed += errors
                print(f"{scenario:12} requests={len(samples)} p95_ms={p95:.1f} unexpected={errors}")
            return int(failed > 0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--k6", action="store_true", help="Run the k6 thresholds instead")
    raise SystemExit(run(parser.parse_args().k6))
