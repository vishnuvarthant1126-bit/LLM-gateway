"""LLM Gateway - FastAPI app.

Run:  uvicorn app.main:app --port 8000
Open: http://localhost:8000/dashboard   (live dashboard)
      http://localhost:8000/docs        (interactive API docs)
"""
from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .metrics import Metrics
from .providers import MockProvider, build_providers
from .ratelimit import TokenBucketLimiter
from .router import AllProvidersFailed, Router

STATIC_DIR = Path(__file__).parent / "static"


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=50)
    max_tokens: int = Field(default=256, ge=1, le=4096)
    provider: str | None = Field(default=None, description="Optionally prefer a specific provider first")


class ModeRequest(BaseModel):
    mode: str = Field(pattern="^(healthy|down|slow|flaky)$")
    failure_rate: float | None = Field(default=None, ge=0, le=1)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    metrics = Metrics()
    providers = build_providers(settings)
    router = Router(providers, settings, metrics)
    limiter = TokenBucketLimiter(settings.api_keys)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        for p in providers:
            await p.aclose()

    app = FastAPI(title="LLM Gateway", version="1.0.0", lifespan=lifespan,
                  description="Unified /chat endpoint with rate limiting, retries, circuit breakers and fallback routing.")
    app.state.router, app.state.metrics, app.state.limiter, app.state.settings = router, metrics, limiter, settings

    def authenticate(x_api_key: str | None, authorization: str | None) -> str:
        key = x_api_key
        if not key and authorization and authorization.lower().startswith("bearer "):
            key = authorization[7:].strip()
        if not key or key not in settings.api_keys:
            raise HTTPException(status_code=401, detail="Missing or invalid API key. Send header 'X-API-Key'.")
        return key

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", include_in_schema=False)
    async def dashboard():
        return FileResponse(STATIC_DIR / "dashboard.html")

    @app.get("/health")
    async def health():
        return {"status": "ok", "providers": len(providers)}

    @app.post("/chat")
    async def chat(body: ChatRequest, response: Response,
                   x_api_key: str | None = Header(default=None),
                   authorization: str | None = Header(default=None)):
        key = authenticate(x_api_key, authorization)

        decision = limiter.check(key)
        if not decision.allowed:
            metrics.record_rate_limited()
            retry = max(1, int(decision.retry_after_s + 0.999))
            raise HTTPException(status_code=429, detail="Rate limit exceeded",
                                headers={"Retry-After": str(retry), "X-RateLimit-Remaining": "0"})

        messages = [m.model_dump() for m in body.messages]
        try:
            result = await router.complete(messages, body.max_tokens, body.provider)
        except AllProvidersFailed as exc:
            return JSONResponse(status_code=503, content={
                "error": "All providers failed",
                "attempts": [a.__dict__ for a in exc.attempts],
                "total_ms": round(exc.total_ms, 1),
            }, headers={"X-RateLimit-Remaining": str(decision.remaining)})

        response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
        response.headers["X-Gateway-Provider"] = result.provider.name
        c = result.completion
        return {
            "id": f"gw-{uuid.uuid4().hex[:12]}",
            "provider": result.provider.name,
            "model": c.model,
            "content": c.text,
            "usage": {"prompt_tokens": c.prompt_tokens, "completion_tokens": c.completion_tokens},
            "cost_usd": round(result.cost_usd, 8),
            "latency_ms": round(result.total_ms, 2),
            "gateway_overhead_ms": round(result.overhead_ms, 3),
            "fallback_used": result.fallback_used,
            "attempts": [a.__dict__ for a in result.attempts],
        }

    @app.get("/metrics")
    async def get_metrics():
        snap = metrics.snapshot()
        snap["providers"] = router.provider_status()
        snap["config"] = {"timeout_s": settings.request_timeout_s, "max_retries": settings.max_retries,
                          "breaker_failure_threshold": settings.breaker_failure_threshold,
                          "breaker_recovery_s": settings.breaker_recovery_s}
        return snap

    # ---- admin endpoints (need a valid API key) ----
    @app.post("/admin/providers/{name}/mode")
    async def set_mode(name: str, body: ModeRequest, x_api_key: str | None = Header(default=None),
                       authorization: str | None = Header(default=None)):
        authenticate(x_api_key, authorization)
        provider = router.by_name.get(name)
        if provider is None:
            raise HTTPException(404, f"Unknown provider '{name}'")
        if not isinstance(provider, MockProvider):
            raise HTTPException(400, "Only mock providers can be put into failure modes")
        provider.set_mode(body.mode, body.failure_rate)
        return {"provider": name, "mode": provider.mode, "failure_rate": provider.failure_rate}

    @app.post("/admin/reset")
    async def reset(x_api_key: str | None = Header(default=None), authorization: str | None = Header(default=None)):
        """Reset circuit breakers, metrics and rate-limit buckets; set mocks healthy."""
        authenticate(x_api_key, authorization)
        for b in router.breakers.values():
            b.reset()
        for p in providers:
            if isinstance(p, MockProvider):
                p.mode, p.failure_rate = "healthy", 0.0
        fresh = Metrics()
        metrics.__dict__.update(fresh.__dict__)
        limiter._buckets.clear()
        return {"status": "reset"}

    return app


app = create_app()
