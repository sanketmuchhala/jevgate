from __future__ import annotations
from typing import Protocol
import httpx
from pydantic import ValidationError
from .schemas import ReviewRequest, ReviewResponse


class SystemTwoError(RuntimeError):
    """The reviewer could not be reached or returned something off-contract."""


class SystemTwo(Protocol):
    async def review(self, request: ReviewRequest) -> ReviewResponse: ...


class HttpSystemTwo:
    """Client for the TypeScript reviewer in system2/ (Vercel AI SDK)."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, timeout_s: float):
        self._client = client
        self._url = base_url.rstrip("/") + "/v1/review"
        self._timeout_s = timeout_s

    async def review(self, request: ReviewRequest) -> ReviewResponse:
        try:
            resp = await self._client.post(self._url, json=request.model_dump(), timeout=self._timeout_s)
            resp.raise_for_status()
            return ReviewResponse.model_validate(resp.json())
        except httpx.HTTPStatusError as exc:
            raise SystemTwoError(f"HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
        except (httpx.HTTPError, ValidationError, ValueError) as exc:
            raise SystemTwoError(f"{type(exc).__name__}: {exc}") from exc
