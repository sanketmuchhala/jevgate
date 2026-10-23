from __future__ import annotations
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
import httpx
from fastapi import FastAPI, Request
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
from .config import Settings
from .policy import load_policy
from .questions import BATTERY
from .router import triage
from .schemas import Policy, QuestionSpec, TriageDecision, TriageRequest
from .system_one import JevSystemOne, SystemOne
from .system_two import HttpSystemTwo, SystemTwo


def create_app(
    *,
    settings: Settings | None = None,
    policy: Policy | None = None,
    system_one: SystemOne | None = None,
    system_two: SystemTwo | None = None,
) -> FastAPI:
    """Tests inject fakes for System-1 and System-2; production builds real clients at startup."""
    settings = settings or Settings.from_env()
    policy = policy or load_policy(settings.policy_path, BATTERY)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            s1 = system_one
            if s1 is None:
                # One client for the process: pooled connections keep TLS handshakes off the hot path.
                client = await stack.enter_async_context(AsyncTypeSafeClient(
                    model=settings.jev_model,
                    base_url=settings.typesafe_base_url,
                    timeout=settings.jev_timeout_s,
                    retry=RetryPolicy(
                        max_retries=settings.jev_max_retries,
                        timeout=settings.jev_timeout_s * (settings.jev_max_retries + 1),
                    ),
                ))
                s1 = JevSystemOne(client, BATTERY, settings.jev_model)
            s2 = system_two
            if s2 is None and settings.system2_url:
                http = await stack.enter_async_context(httpx.AsyncClient())
                s2 = HttpSystemTwo(http, settings.system2_url, settings.system2_timeout_s)
            app.state.system_one = s1
            app.state.system_two = s2
            yield

    app = FastAPI(
        title="JevGate",
        version="0.1.0",
        summary="Calibrated policy triage and institutional risk routing on Jev 1.14",
        lifespan=lifespan,
    )

    @app.get("/healthz")
    async def healthz(request: Request) -> dict[str, object]:
        return {
            "status": "ok",
            "jev_model": settings.jev_model,
            "policy_version": policy.version,
            "system_two": request.app.state.system_two is not None,
        }

    @app.get("/v1/policy", response_model=Policy)
    async def get_policy() -> Policy:
        return policy

    @app.get("/v1/questions")
    async def get_questions() -> dict[str, QuestionSpec]:
        return BATTERY

    @app.post("/v1/triage", response_model=TriageDecision)
    async def post_triage(body: TriageRequest, request: Request) -> TriageDecision:
        return await triage(
            body,
            policy=policy,
            battery=BATTERY,
            system_one=request.app.state.system_one,
            system_two=request.app.state.system_two,
        )

    return app


app = create_app()
