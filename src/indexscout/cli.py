"""Command-line interface. Uses the same functions as the MCP tools."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import os
import stat
import sys
from typing import Any

from indexscout import __version__, auth, server
from indexscout import validate as v
from indexscout.gsc import GSCError

EXPECTED_TOOLS = {
    "gsc_capabilities",
    "gsc_list_properties",
    "gsc_search_analytics",
    "gsc_site_snapshot",
    "gsc_diagnose_change",
    "gsc_find_opportunities",
    "gsc_page_analysis",
    "gsc_query_analysis",
    "gsc_find_cannibalization",
    "gsc_inspect_url",
    "gsc_indexing_audit",
    "gsc_list_sitemaps",
}
KNOWN_ERRORS = (v.ValidationError, auth.AuthError, GSCError)


def _print(line: str = "") -> None:
    print(auth.redact(line))


# --- auth -----------------------------------------------------------------------------------


def cmd_auth_login(args: argparse.Namespace) -> int:
    if auth.auth_method() == "service_account":
        _print("INDEXSCOUT_SERVICE_ACCOUNT_FILE is set; service accounts need no login.")
        return 0
    result = auth.login(open_browser=not args.no_browser)
    _print(f"Logged in. Scopes: {', '.join(result['scopes'])}. Token store: {result['token_store']}.")
    if result["token_store"] == "file":  # noqa: S105
        _print(f"WARNING: {auth.FILE_WARNING}")
    return 0


def cmd_auth_status(_: argparse.Namespace) -> int:
    st = auth.status()
    _print(f"Method:        {st['method']}")
    _print(f"Authenticated: {st['authenticated']}")
    _print(f"Scopes:        {', '.join(st['scopes']) or '-'}")
    if "token_store" in st:
        _print(f"Token store:   {st['token_store']}")
        _print(
            f"Client file:   {'present' if st.get('client_secrets_present') else 'missing'} "
            f"({auth.client_secrets_path()})"
        )
    for w in st["warnings"]:
        _print(f"WARNING: {w}")
    return 0 if st["authenticated"] else 1


def cmd_auth_logout(_: argparse.Namespace) -> int:
    auth.delete_token()
    _print(
        "Removed the stored IndexScout refresh token. Revoke access at https://myaccount.google.com/permissions"
    )
    return 0


# --- data commands --------------------------------------------------------------------------


def cmd_properties(_: argparse.Namespace) -> int:
    res = asyncio.run(server.gsc_list_properties())
    _print(res["summary"])
    for p in res["results"]:
        flag = "" if p["allowed_by_local_policy"] else "  (blocked by allowlist)"
        _print(f"  {p['property']}  [{p['permission_level']}]{flag}")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    res = asyncio.run(server.gsc_site_snapshot(args.property, days=args.days, limit=args.limit))
    if args.json:
        print(server.dumps(res))
        return 0
    r, prov = res["results"], res["provenance"]
    t = r["totals"]
    _print(res["summary"])
    _print(
        f"Periods: {prov['current_period']['start']}..{prov['current_period']['end']} vs "
        f"{prov['baseline_period']['start']}..{prov['baseline_period']['end']} (inclusive, PT, {prov['data_state']})"
    )
    _print()
    _print(f"{'':12}{'current':>12}{'baseline':>12}{'change':>10}")
    for m in ("clicks", "impressions"):
        pct = t["change"].get(f"{m}_change_pct")
        _print(
            f"{m:12}{t['current'][m]:>12,}{t['baseline'][m]:>12,}{(f'{pct:+.1f}%' if pct is not None else 'n/a'):>10}"
        )
    _print(
        f"{'ctr':12}{t['current']['ctr'] * 100:>11.2f}%{t['baseline']['ctr'] * 100:>11.2f}%"
        f"{t['change'].get('ctr_change_pp', 0):>+9.2f}pp"
    )
    cp, bp = t["current"]["position"], t["baseline"]["position"]
    _print(f"{'avg position':12}{cp if cp is not None else '-':>12}{bp if bp is not None else '-':>12}")
    for title, key, field in (
        ("Top losing pages", "top_losing_pages", "page"),
        ("Top gaining pages", "top_gaining_pages", "page"),
        ("Top losing queries", "top_losing_queries", "query"),
        ("Top gaining queries", "top_gaining_queries", "query"),
    ):
        if r[key]:
            _print()
            _print(f"{title} (clicks):")
            for item in r[key]:
                _print(f"  {item['change']:>+8,.0f}  {item[field]}")
    for w in res["warnings"]:
        _print(f"WARNING: {w}")
    if res["recommended_next_calls"]:
        _print()
        _print("Suggested next investigations:")
        for c in res["recommended_next_calls"]:
            _print(f"  {c['tool']}: {c['reason']}")
    return 0


def cmd_serve(_: argparse.Namespace) -> int:
    server.run_stdio()
    return 0


# --- doctor ---------------------------------------------------------------------------------


def _check(results: list[tuple[str, str, str]], level: str, name: str, detail: str) -> None:
    results.append((level, name, detail))


async def _mcp_check() -> tuple[str, str]:
    from mcp import Client

    async with Client(server.mcp) as c:
        tools = (await c.list_tools()).tools
        names = {t.name for t in tools}
        if names != EXPECTED_TOOLS:
            return "FAIL", f"unexpected tool set: {sorted(names ^ EXPECTED_TOOLS)}"
        if not all(t.annotations and t.annotations.read_only_hint for t in tools):
            return "FAIL", "a tool is not marked read-only"
        if not (c.instructions or "").startswith("IndexScout gives read-only"):
            return "FAIL", "server instructions missing"
        return "PASS", f"{len(tools)} read-only tools, instructions present"


def cmd_doctor(_: argparse.Namespace) -> int:
    results: list[tuple[str, str, str]] = []
    _check(results, "PASS" if sys.version_info >= (3, 11) else "FAIL", "python", sys.version.split()[0])
    for dist in (
        "mcp",
        "google-api-python-client",
        "google-auth",
        "google-auth-oauthlib",
        "keyring",
        "filelock",
    ):
        try:
            _check(results, "PASS", f"dependency {dist}", importlib.metadata.version(dist))
        except importlib.metadata.PackageNotFoundError:
            _check(results, "FAIL", f"dependency {dist}", "not installed")

    st = auth.status()
    if st["method"] == "service_account":
        ok = st.get("service_account_file_present")
        _check(
            results,
            "PASS" if ok else "FAIL",
            "credentials",
            "service account file " + ("found" if ok else "missing"),
        )
    else:
        present = st.get("client_secrets_present")
        _check(
            results,
            "PASS" if present or st["authenticated"] else "WARN",
            "credentials",
            "OAuth client file found"
            if present
            else f"OAuth client file missing: {auth.client_secrets_path()}",
        )
        store = st.get("token_store")
        if store == "keyring":
            _check(results, "PASS", "token storage", "OS keyring")
        else:
            tf = auth.token_file()
            mode = stat.S_IMODE(os.stat(tf).st_mode) if tf.exists() else None
            ok = mode is None or mode == 0o600
            _check(
                results,
                "WARN" if ok else "FAIL",
                "token storage",
                f"file fallback ({'mode 0600' if mode else 'no token yet'}); {auth.FILE_WARNING}"
                if ok
                else f"token file mode is {oct(mode or 0)}; expected 0600",
            )
    scopes = st["scopes"]
    if not st["authenticated"]:
        _check(results, "WARN", "authentication", "not authenticated; run `indexscout auth login`")
    elif scopes == [auth.READONLY_SCOPE] or set(scopes) == {auth.READONLY_SCOPE}:
        _check(results, "PASS", "read-only scope", auth.READONLY_SCOPE)
    else:
        _check(results, "FAIL", "read-only scope", f"unexpected scopes granted: {scopes}")

    try:
        allowed = v.allowlist()
        _check(
            results,
            "PASS",
            "property allowlist",
            "not set (all accessible properties allowed)"
            if allowed is None
            else f"{len(allowed)} properties",
        )
    except v.ValidationError as exc:
        _check(results, "FAIL", "property allowlist", str(exc))

    if st["authenticated"]:
        try:
            res = asyncio.run(server.gsc_list_properties())
            _check(results, "PASS", "Google API", f"reachable; {len(res['results'])} properties")
        except KNOWN_ERRORS as exc:
            _check(results, "FAIL", "Google API", str(exc))
    else:
        _check(results, "SKIP", "Google API", "skipped until authenticated")

    try:
        level, detail = asyncio.run(_mcp_check())
        _check(results, level, "MCP initialization", detail)
    except Exception as exc:
        _check(results, "FAIL", "MCP initialization", type(exc).__name__)

    sample = 'access_token="ya29.abc" refresh_token=1//xyz client_secret: GOCSPX-123'
    red = auth.redact(sample)
    ok = not any(s in red for s in ("ya29.abc", "1//xyz", "GOCSPX-123"))
    _check(
        results,
        "PASS" if ok else "FAIL",
        "secret redaction",
        "tokens are masked" if ok else "redaction failed",
    )

    for level, name, detail in results:
        _print(f"{level:5} {name}: {detail}")
    failed = any(level == "FAIL" for level, _, _ in results)
    _print()
    _print(
        "Doctor found problems." if failed else "Doctor passed (WARN/SKIP items need no action unless noted)."
    )
    return 1 if failed else 0


# --- entry point ----------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="indexscout", description="Read-only Google Search Console MCP server."
    )
    parser.add_argument("--version", action="version", version=f"indexscout {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    a = sub.add_parser("auth", help="Manage Google authentication.").add_subparsers(
        dest="auth_command", required=True
    )
    login = a.add_parser("login", help="Authorize read-only access in the browser.")
    login.add_argument(
        "--no-browser", action="store_true", help="Print the URL instead of opening a browser."
    )
    login.set_defaults(func=cmd_auth_login)
    a.add_parser("status", help="Show authentication status.").set_defaults(func=cmd_auth_status)
    a.add_parser("logout", help="Delete the stored refresh token.").set_defaults(func=cmd_auth_logout)

    sub.add_parser("properties", help="List accessible properties.").set_defaults(func=cmd_properties)
    sub.add_parser("doctor", help="Check installation, credentials, and security.").set_defaults(
        func=cmd_doctor
    )
    sub.add_parser("serve", help="Run the MCP server over stdio.").set_defaults(func=cmd_serve)
    snap = sub.add_parser("snapshot", help="Human-readable property snapshot.")
    snap.add_argument("property", help="e.g. sc-domain:example.com")
    snap.add_argument("--days", type=int, default=28)
    snap.add_argument("--limit", type=int, default=10)
    snap.add_argument("--json", action="store_true", help="Print the full MCP response.")
    snap.set_defaults(func=cmd_snapshot)
    return parser


def main(argv: list[str] | None = None) -> None:
    auth.configure_logging()
    args = build_parser().parse_args(argv)
    try:
        code: Any = args.func(args)
    except KNOWN_ERRORS as exc:
        print(f"indexscout: {auth.redact(str(exc))}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        code = 130
    sys.exit(code)
