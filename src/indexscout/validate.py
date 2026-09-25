"""Input validation for properties, URLs, dates, dimensions, and filters.

Every value that reaches Google passes through this module first. All errors raise
ValidationError with a message an agent can act on.
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

GSC_TZ = ZoneInfo("America/Los_Angeles")
# Search Console keeps about 16 months of performance data.
MAX_HISTORY_DAYS = 16 * 31

DIMENSIONS = ("query", "page", "date", "country", "device", "searchAppearance")
FILTER_DIMENSIONS = ("query", "page", "country", "device", "searchAppearance")
SEARCH_TYPES = ("web", "image", "video", "news", "discover", "googleNews")
# Discover and Google News report no query dimension and no average position.
NO_QUERY_TYPES = ("discover", "googleNews")
OPERATORS = ("equals", "notEquals", "contains", "notContains", "includingRegex", "excludingRegex")
DATA_STATES = ("final", "all")
DEVICES = ("DESKTOP", "MOBILE", "TABLET")
METRICS = ("clicks", "impressions")

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁦-⁩]")


class ValidationError(ValueError):
    """The caller supplied an unusable argument."""


def clean_text(value: object, limit: int = 2048) -> str:
    """Make an untrusted GSC string safe to show: no control or bidi characters, bounded length."""
    text = _CONTROL.sub("", str(value))
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- properties -----------------------------------------------------------------------------


def normalize_property(prop: str) -> str:
    """Return the canonical GSC property identifier or raise ValidationError."""
    prop = (prop or "").strip()
    if prop.startswith("sc-domain:"):
        domain = prop[len("sc-domain:") :].strip().lower().rstrip(".")
        if not re.fullmatch(r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9-]{2,63}", domain):
            raise ValidationError(f"Invalid domain property {prop!r}. Use the form sc-domain:example.com.")
        return f"sc-domain:{domain}"
    parts = urlsplit(prop)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValidationError(
            f"Invalid property {clean_text(prop, 200)!r}. Use sc-domain:example.com or a URL-prefix "
            "property such as https://www.example.com/. Call gsc_list_properties for exact identifiers."
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValidationError("URL-prefix properties cannot contain credentials, a query, or a fragment.")
    if not parts.path.endswith("/"):
        raise ValidationError(f"URL-prefix properties must end with '/'. Did you mean {prop}/ ?")
    return f"{parts.scheme}://{_netloc(parts.scheme, parts.hostname, parts.port)}{parts.path}"


def _netloc(scheme: str, host: str, port: int | None) -> str:
    host = host.lower().rstrip(".")
    if port is None or (scheme, port) in (("http", 80), ("https", 443)):
        return host
    return f"{host}:{port}"


def url_in_property(url: str, prop: str) -> bool:
    """True when `url` is covered by the GSC property `prop`.

    Domain properties cover every scheme, subdomain, port, and path of the domain.
    URL-prefix properties require the same scheme, host, and port, and a path under the prefix.
    """
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return False
    host = parts.hostname.lower().rstrip(".")
    prop = normalize_property(prop)
    if prop.startswith("sc-domain:"):
        domain = prop[len("sc-domain:") :]
        return host == domain or host.endswith("." + domain)
    pp = urlsplit(prop)
    if parts.scheme != pp.scheme or _netloc(parts.scheme, host, port) != pp.netloc:
        return False
    return (parts.path or "/").startswith(pp.path)


def require_url_in_property(url: str, prop: str) -> str:
    url = (url or "").strip()
    if len(url) > 2048:
        raise ValidationError("URL is longer than 2048 characters.")
    if not url_in_property(url, prop):
        raise ValidationError(
            f"URL {clean_text(url, 200)!r} does not belong to property {prop!r}. "
            "Inspect only URLs covered by the selected property."
        )
    return url


def allowlist() -> frozenset[str] | None:
    """Exact property identifiers from INDEXSCOUT_ALLOWED_PROPERTIES, or None when unrestricted."""
    raw = os.environ.get("INDEXSCOUT_ALLOWED_PROPERTIES", "").strip()
    if not raw:
        return None
    return frozenset(normalize_property(p) for p in raw.split(",") if p.strip())


def is_allowed(prop: str) -> bool:
    allowed = allowlist()
    return allowed is None or normalize_property(prop) in allowed


def require_property(prop: str) -> str:
    """Normalize a property and enforce the local allowlist."""
    prop = normalize_property(prop)
    if not is_allowed(prop):
        raise ValidationError(
            f"Property {prop!r} is not in INDEXSCOUT_ALLOWED_PROPERTIES. "
            "Local policy blocks it even if the Google account can access it."
        )
    return prop


# --- dates ----------------------------------------------------------------------------------


def today_pt() -> date:
    """Today's calendar date in Search Console's reporting time zone."""
    return datetime.now(GSC_TZ).date()


def parse_date(value: str, name: str = "date") -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{name} must use YYYY-MM-DD format, got {clean_text(value, 40)!r}.") from None


def check_range(start: date, end: date) -> None:
    if start > end:
        raise ValidationError(f"start_date {start} is after end_date {end}.")
    today = today_pt()
    if end > today:
        raise ValidationError(f"end_date {end} is in the future (today is {today} in America/Los_Angeles).")
    if (today - start).days > MAX_HISTORY_DAYS:
        raise ValidationError(f"start_date {start} is older than Search Console's ~16-month data retention.")


def days_in(start: date, end: date) -> int:
    return (end - start).days + 1


def previous_period(start: date, end: date) -> tuple[date, date]:
    """The equal-length period that ends the day before `start`."""
    n = days_in(start, end)
    return start - timedelta(days=n), start - timedelta(days=1)


def check_days(days: int) -> int:
    if not 1 <= days <= 240:
        raise ValidationError("days must be between 1 and 240 so both compared periods fit in retention.")
    return days


# --- dimensions and filters -------------------------------------------------------------------


def check_search_type(search_type: str) -> str:
    if search_type not in SEARCH_TYPES:
        raise ValidationError(f"search_type must be one of {', '.join(SEARCH_TYPES)}.")
    return search_type


def check_dimensions(dimensions: list[str], search_type: str = "web") -> list[str]:
    if len(dimensions) != len(set(dimensions)):
        raise ValidationError("dimensions must not repeat.")
    for d in dimensions:
        if d not in DIMENSIONS:
            raise ValidationError(f"Unknown dimension {clean_text(d, 40)!r}. Use: {', '.join(DIMENSIONS)}.")
    if search_type in NO_QUERY_TYPES and "query" in dimensions:
        raise ValidationError(f"Search type {search_type!r} does not report the query dimension.")
    return list(dimensions)


def check_filter(f: dict[str, str]) -> dict[str, str]:
    dim = f.get("dimension")
    op = f.get("operator", "equals")
    expr = f.get("expression")
    if dim not in FILTER_DIMENSIONS:
        raise ValidationError(f"Filter dimension must be one of {', '.join(FILTER_DIMENSIONS)}.")
    if op not in OPERATORS:
        raise ValidationError(f"Filter operator must be one of {', '.join(OPERATORS)}.")
    if not isinstance(expr, str) or not expr or len(expr) > 4096:
        raise ValidationError("Filter expression must be a non-empty string of at most 4096 characters.")
    if dim == "device" and op in ("equals", "notEquals"):
        expr = expr.upper()
        if expr not in DEVICES:
            raise ValidationError(f"Device must be one of {', '.join(DEVICES)}.")
    if dim == "country" and op in ("equals", "notEquals"):
        expr = expr.lower()
        if not re.fullmatch(r"[a-z]{3}", expr):
            raise ValidationError("Country must be an ISO 3166-1 alpha-3 code, for example 'usa'.")
    return {"dimension": dim, "operator": op, "expression": expr}


def check_filters(filters: list[dict[str, str]] | None) -> list[dict[str, str]]:
    filters = filters or []
    if len(filters) > 10:
        raise ValidationError("Use at most 10 filters.")
    return [check_filter(f) for f in filters]


def check_limit(value: int, name: str, maximum: int) -> int:
    if not 1 <= value <= maximum:
        raise ValidationError(f"{name} must be between 1 and {maximum}.")
    return value
