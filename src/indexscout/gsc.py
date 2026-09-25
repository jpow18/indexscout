"""Thin read-only access to the Search Console API, plus live sitemap files.

Only four API read methods exist: sites.list, searchanalytics.query, urlInspection.index.inspect,
and sitemaps.list. There is no code path to any write method or to the Indexing API.

The one non-Google request is an HTTP GET of a sitemap file that belongs to the selected
property, because the API reports sitemap status but not the URLs inside a sitemap.
"""

from __future__ import annotations

import gzip
import io
import json
import urllib.error
import urllib.request
from typing import Any, Protocol

import anyio

from indexscout import __version__
from indexscout.validate import url_in_property

API_ROW_LIMIT = 25_000  # Google's maximum rowLimit per request.
MAX_SITEMAP_DOWNLOAD = 10 * 1024 * 1024  # bytes read from the network
MAX_SITEMAP_XML = 50 * 1024 * 1024  # sitemaps.org limit for an uncompressed file
USER_AGENT = f"IndexScout/{__version__} (+https://github.com/jpow18/indexscout)"


class GSCError(RuntimeError):
    """A Search Console request or sitemap fetch failed. The message is safe to show to an agent."""


class Client(Protocol):
    async def list_sites(self) -> list[dict[str, Any]]: ...
    async def query(self, prop: str, body: dict[str, Any]) -> dict[str, Any]: ...
    async def inspect(self, prop: str, url: str) -> dict[str, Any]: ...
    async def list_sitemaps(self, prop: str) -> list[dict[str, Any]]: ...
    async def fetch_sitemap_file(self, prop: str, url: str) -> bytes: ...


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

    async def fetch_sitemap_file(self, prop: str, url: str) -> bytes:
        return await anyio.to_thread.run_sync(fetch_sitemap_file, prop, url)


class _PropertyRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to URLs inside the property (e.g. http -> https on the same site)."""

    def __init__(self, prop: str) -> None:
        self.prop = prop

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if not url_in_property(newurl, self.prop):
            raise GSCError("Sitemap redirected outside the property; IndexScout did not follow it.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_sitemap_file(prop: str, url: str) -> bytes:
    """GET one sitemap file (plain or gzip) from inside `prop`, with size and redirect limits."""
    if not url_in_property(url, prop):
        raise GSCError("Sitemap URL is outside the property; IndexScout did not fetch it.")
    opener = urllib.request.build_opener(_PropertyRedirects(prop))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - scheme checked above
    try:
        with opener.open(request, timeout=30) as resp:
            data: bytes = resp.read(MAX_SITEMAP_DOWNLOAD + 1)
    except urllib.error.HTTPError as exc:
        raise GSCError(f"Sitemap fetch failed: HTTP {exc.code}.") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GSCError(f"Sitemap fetch failed ({type(exc).__name__}).") from None
    if len(data) > MAX_SITEMAP_DOWNLOAD:
        raise GSCError("Sitemap file is larger than 10 MB; IndexScout did not read it.")
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.GzipFile(fileobj=io.BytesIO(data)).read(MAX_SITEMAP_XML + 1)
        except (OSError, EOFError):
            raise GSCError("Sitemap is not valid gzip.") from None
        if len(data) > MAX_SITEMAP_XML:
            raise GSCError("Uncompressed sitemap is larger than 50 MB; IndexScout did not read it.")
    return data
