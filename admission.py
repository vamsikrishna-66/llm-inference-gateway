import asyncio
import os

from starlette.responses import JSONResponse

# Increment and expiry must be atomic even when multiple gateways share the key.
RATE_LIMIT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('PEXPIRE', KEYS[1], ARGV[1]) end
return {count, redis.call('PTTL', KEYS[1])}
"""


class Admission:
    def __init__(self, app):
        self.app = app
        active = int(os.getenv("MAX_ACTIVE", "4"))
        queued = int(os.getenv("MAX_QUEUED", "8"))
        self.capacity = active + queued
        self.timeout = float(os.getenv("QUEUE_TIMEOUT", "5"))
        self.limit = int(os.getenv("REQUESTS_PER_MINUTE", "60"))
        if min(active, self.timeout, self.limit) <= 0 or queued < 0:
            raise ValueError("Admission limits must be positive")
        self.active = asyncio.Semaphore(active)
        self.pending = 0

    async def check_rate(self):
        from redis.asyncio import Redis
        from redis.exceptions import RedisError

        try:
            async with Redis.from_url(
                os.environ["REDIS_URL"], socket_timeout=1, socket_connect_timeout=1
            ) as redis:
                count, ttl = await redis.eval(RATE_LIMIT, 1, "inference:generate", 60000)
        except RedisError:
            return JSONResponse({"detail": "Rate limiter unavailable"}, status_code=503)
        if count > self.limit:
            return JSONResponse(
                {"detail": "Request rate exceeded"},
                status_code=429,
                headers={"Retry-After": str(max(1, (ttl + 999) // 1000))},
            )
        return None

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or (scope["method"], scope["path"]) != ("POST", "/generate"):
            await self.app(scope, receive, send)
            return
        if self.pending >= self.capacity:
            await JSONResponse({"detail": "Inference queue full"}, status_code=503)(scope, receive, send)
            return
        self.pending += 1
        try:
            if os.getenv("REDIS_URL"):
                rejection = await self.check_rate()
                if rejection is not None:
                    await rejection(scope, receive, send)
                    return
            try:
                async with asyncio.timeout(self.timeout):
                    await self.active.acquire()
            except TimeoutError:
                await JSONResponse({"detail": "Inference queue timed out"}, status_code=503)(
                    scope, receive, send
                )
                return
            try:
                await self.app(scope, receive, send)
            finally:
                self.active.release()
        finally:
            self.pending -= 1
