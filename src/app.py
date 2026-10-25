from __future__ import annotations
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
import json
from pathlib import Path
import secrets
import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy
from .config import Settings
from .cases import CaseService
from .policy import load_policy
from .questions import BATTERY
from .router import triage
from .schemas import Policy, QuestionSpec, TriageDecision, TriageRequest, Verdict
from .store import CaseConflict, CaseNotFound, CaseStore
from .system_one import JevSystemOne, SystemOne
from .system_two import HttpSystemTwo, SystemTwo, SystemTwoError
from .workbench_schemas import (
    CaseAssignmentUpdate, CaseCreateRequest, CaseDetail, CaseStatus, CaseSummary,
    RuleDispositionUpdate, VersionCreateRequest,
)


def create_app(
    *,
    settings: Settings | None = None,
    policy: Policy | None = None,
    system_one: SystemOne | None = None,
    system_two: SystemTwo | None = None,
    case_store: CaseStore | None = None,
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
            if case_store is None:
                app.state.case_store = CaseStore(settings.db_path)
                stack.callback(app.state.case_store.close)
            app.state.cases = CaseService(app.state.case_store, policy=policy, battery=BATTERY,
                                          system_one=s1, system_two=s2)
            yield

    app = FastAPI(
        title="JevGate",
        version="0.1.0",
        summary="Calibrated policy triage and institutional risk routing on Jev 1.14",
        lifespan=lifespan,
    )
    app.state.case_store = case_store

    @app.exception_handler(CaseNotFound)
    async def case_not_found(request: Request, exc: CaseNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(CaseConflict)
    async def case_conflict(request: Request, exc: CaseConflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    async def authorize(request: Request):
        if settings.api_token:
            expected = f"Bearer {settings.api_token}".encode()
            supplied = request.headers.get("authorization", "").encode()
            if not secrets.compare_digest(supplied, expected):
                raise HTTPException(401, "A valid bearer token is required", headers={"WWW-Authenticate": "Bearer"})

    cases_api = APIRouter(prefix="/v1", dependencies=[Depends(authorize)])

    @cases_api.post("/cases", response_model=CaseDetail, status_code=201)
    async def post_case(body: CaseCreateRequest, request: Request):
        return await request.app.state.cases.create_case(body)

    @cases_api.get("/cases", response_model=list[CaseSummary])
    async def list_cases(request: Request, status: CaseStatus | None = None,
                         office: str | None = None, verdict: Verdict | None = None):
        return request.app.state.cases.list_cases(status=status, office=office, verdict=verdict)

    @cases_api.get("/cases/{case_id}", response_model=CaseDetail)
    async def get_case(case_id: str, request: Request, version: int | None = None):
        return request.app.state.case_store.get_case(case_id, version)

    @cases_api.post("/cases/{case_id}/versions", response_model=CaseDetail, status_code=201)
    async def post_version(case_id: str, body: VersionCreateRequest, request: Request):
        return await request.app.state.cases.create_version(case_id, body)

    @cases_api.patch("/cases/{case_id}/versions/{version_number}/rules/{rule_id}", response_model=CaseDetail)
    async def patch_rule(case_id: str, version_number: int, rule_id: str,
                         body: RuleDispositionUpdate, request: Request):
        try:
            return request.app.state.cases.update_disposition(case_id, version_number, rule_id, body)
        except CaseNotFound:
            raise
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @cases_api.post("/cases/{case_id}/versions/{version_number}/ground", response_model=CaseDetail)
    async def ground_version(case_id: str, version_number: int, request: Request):
        try:
            return await request.app.state.cases.ground_case_version(case_id, version_number)
        except SystemTwoError as exc:
            # Provider errors may contain submitted text: never expose or log their body.
            raise HTTPException(503, "Evidence grounding unavailable") from exc

    @cases_api.patch("/cases/{case_id}", response_model=CaseDetail)
    async def patch_case(case_id: str, body: CaseAssignmentUpdate, request: Request):
        try:
            return request.app.state.cases.update_assignment(case_id, body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @cases_api.get("/samples")
    async def get_samples() -> list[dict[str, object]]:
        samples = []
        for path in sorted((Path(__file__).resolve().parent.parent / "samples").glob("*.json")):
            try:
                sample = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(sample, dict) and isinstance(sample.get("document"), str):
                samples.append({"id": path.stem, "title": path.stem.replace("_", " ").title(), **sample})
        return samples

    app.include_router(cases_api)

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

    web = Path(__file__).resolve().parent / "web"
    if web.is_dir():
        app.mount("/app", StaticFiles(directory=web, html=True), name="web")

        @app.get("/", include_in_schema=False)
        async def index():
            return RedirectResponse("/app/")

    return app


app = create_app()
