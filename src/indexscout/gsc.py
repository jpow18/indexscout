"""Thin read-only access to the Search Console API.

Only four read methods exist: sites.list, searchanalytics.query, urlInspection.index.inspect,
and sitemaps.list. There is no code path to any write method or to the Indexing API.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

import anyio

API_ROW_LIMIT = 25_000  # Google's maximum rowLimit per request.


class GSCError(RuntimeError):
    """A Google API call failed. The message is safe to show to an agent."""


class Client(Protocol):
    async def list_sites(self) -> list[dict[str, Any]]: ...
    async def query(self, prop: str, body: dict[str, Any]) -> dict[str, Any]: ...
    async def inspect(self, prop: str, url: str) -> dict[str, Any]: ...
    async def list_sitemaps(self, prop: str) -> list[dict[str, Any]]: ...


def _error_message(exc: Exception) -> str:
    from googleapiclient.errors import HttpError

    if isinstance(exc, HttpError):
        status = exc.resp.status if exc.resp is not None else "?"
        reason = ""
        try:
            reason = json.loads(exc.content)["error"]["message"]
        except Exception:
            reason = getattr(exc, "reason", "") or ""
        hint = {
            401: "Credentials were rejected. Run `indexscout auth login` again.",
            403: "The account lacks access to this property or the quota is exhausted.",
            429: "Search Console quota exceeded. Wait about 15 minutes and retry with fewer calls.",
        }.get(status if isinstance(status, int) else 0, "")
        return f"Google API error {status}: {reason[:300]} {hint}".strip()
    from google.auth.exceptions import RefreshError

    if isinstance(exc, RefreshError):
        # Do not delete the token here: a refresh can fail for transient reasons.
        return "Google rejected the stored refresh token. Run `indexscout auth login` again."
    return f"Google API request failed ({type(exc).__name__})."


class GoogleClient:
    """Real client. Each call gets its own HTTP object because httplib2 is not thread-safe."""

    def __init__(self, credentials: Any) -> None:
        from googleapiclient.discovery import build

        self._creds = credentials
        self._service = build("searchconsole", "v1", credentials=credentials, cache_discovery=False)

    async def _run(self, request: Any) -> Any:
        import google_auth_httplib2
        import httplib2

        def call() -> Any:
            http = google_auth_httplib2.AuthorizedHttp(self._creds, http=httplib2.Http(timeout=60))
            return request.execute(http=http, num_retries=2)

        try:
            return await anyio.to_thread.run_sync(call)
        except Exception as exc:
            raise GSCError(_error_message(exc)) from None

    async def list_sites(self) -> list[dict[str, Any]]:
        res = await self._run(self._service.sites().list())
        return list(res.get("siteEntry", []))

    async def query(self, prop: str, body: dict[str, Any]) -> dict[str, Any]:
        res: dict[str, Any] = await self._run(self._service.searchanalytics().query(siteUrl=prop, body=body))
        return res

    async def inspect(self, prop: str, url: str) -> dict[str, Any]:
        body = {"inspectionUrl": url, "siteUrl": prop}
        res = await self._run(self._service.urlInspection().index().inspect(body=body))
        return dict(res.get("inspectionResult", {}))

    async def list_sitemaps(self, prop: str) -> list[dict[str, Any]]:
        res = await self._run(self._service.sitemaps().list(siteUrl=prop))
        return list(res.get("sitemap", []))
