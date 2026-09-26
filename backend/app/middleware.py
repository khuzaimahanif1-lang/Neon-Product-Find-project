from time import monotonic
from uuid import uuid4

import structlog
from cachetools import TTLCache
from starlette.responses import JSONResponse

log = structlog.get_logger()


class RequestPolicy:
    """Process-local rate limits for the local MVP; use shared storage for multiple workers."""

    def __init__(self, settings):
        self.settings = settings
        self.counters = TTLCache(maxsize=10_000, ttl=60)

    async def __call__(self, request, call_next):
        identifier = str(uuid4())
        started = monotonic()
        origin = request.headers.get("origin")
        if (
            request.method not in {"GET", "HEAD", "OPTIONS"}
            and origin
            and origin not in self.settings.allowed_origins
        ):
            return JSONResponse({"detail": "Request origin is not allowed."}, status_code=403)
        group = (
            "auth"
            if request.url.path in {"/api/auth/signup", "/api/auth/login"}
            else "search"
            if request.url.path in {"/api/products", "/api/recommendations"}
            else None
        )
        if group and request.method != "OPTIONS":
            key = (request.client.host if request.client else "unknown", group)
            old_count, window = self.counters.get(key, (0, started))
            limit = (
                self.settings.auth_limit_per_minute
                if group == "auth"
                else self.settings.search_limit_per_minute
            )
            if started - window >= 60:
                old_count, window = 0, started
            count = old_count + 1
            self.counters[key] = (count, window)
            if count > limit:
                return JSONResponse(
                    {"detail": "Too many requests. Try again shortly."},
                    status_code=429,
                    headers={"Retry-After": "60", "X-Request-ID": identifier},
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = identifier
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/auth") or request.url.path.startswith("/api/me"):
            response.headers["Cache-Control"] = "no-store"
        log.info(
            "request",
            request_id=identifier,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((monotonic() - started) * 1000, 2),
        )
        return response
