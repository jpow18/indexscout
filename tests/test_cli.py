import json
import os
import pathlib
import sys

import anyio
import pytest
from conftest import PROP
from mcp import Client, StdioServerParameters

from indexscout import cli


def run_cli(*argv):
    with pytest.raises(SystemExit) as exc:
        cli.main(list(argv))
    return exc.value.code


def test_doctor_passes_without_credentials(capsys):
    assert run_cli("doctor") == 0
    out = capsys.readouterr().out
    assert "PASS  MCP initialization: 12 read-only tools" in out
    assert "WARN  authentication" in out and "FAIL" not in out


def test_doctor_fails_on_bad_allowlist(capsys, monkeypatch):
    monkeypatch.setenv("INDEXSCOUT_ALLOWED_PROPERTIES", "not a property")
    assert run_cli("doctor") == 1
    assert "FAIL  property allowlist" in capsys.readouterr().out


def test_auth_status_unauthenticated(capsys):
    assert run_cli("auth", "status") == 1
    assert "Authenticated: False" in capsys.readouterr().out


def test_snapshot_uses_shared_implementation(fake, capsys):
    assert run_cli("snapshot", PROP, "--days", "28") == 0
    out = capsys.readouterr().out
    assert "Top losing pages (clicks):" in out and "/pricing" in out and "gsc_diagnose_change" in out
    assert run_cli("snapshot", PROP, "--json") == 0
    assert set(json.loads(capsys.readouterr().out)) >= {"summary", "provenance"}


def test_properties_command(fake, capsys, monkeypatch):
    monkeypatch.setenv("INDEXSCOUT_ALLOWED_PROPERTIES", PROP)
    assert run_cli("properties") == 0
    assert "(blocked by allowlist)" in capsys.readouterr().out


def test_known_errors_exit_cleanly(capsys):
    assert run_cli("snapshot", "bogus") == 2
    assert "Invalid property" in capsys.readouterr().err


def test_stdio_server_smoke(tmp_path):
    """Start the real console entry point over stdio, unauthenticated, and call a tool."""
    env = {**os.environ, "INDEXSCOUT_CONFIG_DIR": str(tmp_path), "INDEXSCOUT_TOKEN_STORE": "file"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "indexscout", "serve"], env=env)

    async def go():
        async with Client(params) as c:
            tools = (await c.list_tools()).tools
            res = await c.call_tool("gsc_capabilities", {})
            return c.instructions, len(tools), json.loads(res.content[0].text)

    instructions, n, body = anyio.run(go)
    assert instructions.startswith("IndexScout") and n == 12
    assert body["results"]["authentication"]["authenticated"] is False


def test_serve_opens_no_network_listener():
    src = pathlib.Path(cli.__file__).read_text() + pathlib.Path(cli.server.__file__).read_text()
    assert 'mcp.run("stdio")' in src
    for transport in ('"sse"', '"streamable-http"', "host=", "port="):
        assert transport not in src
