"""Repository hygiene: no credentials in tracked files, and ignore rules cover secret files."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SKIP = {".git", ".venv", "dist", "build", ".mypy_cache", ".ruff_cache", ".pytest_cache", "__pycache__"}
REAL_SECRET = re.compile(
    r"ya29\.[\w-]{40,}|1//0[\w-]{40,}|GOCSPX-[\w-]{24,}|-----BEGIN [A-Z ]*PRIVATE KEY-----\s*\n\s*MII"
    r"|\"private_key_id\"\s*:\s*\"[0-9a-f]{40}\"|/home/[a-z]+/|/Users/[A-Za-z]+/"
)


def files():
    for p in ROOT.rglob("*"):
        if p.is_file() and not SKIP & set(p.relative_to(ROOT).parts) and p.suffix not in {".lock", ".mcpb"}:
            yield p


def test_no_secrets_or_personal_paths_in_repo():
    hits = []
    for p in files():
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        hits += [f"{p.relative_to(ROOT)}: {m.group(0)[:20]}" for m in REAL_SECRET.finditer(text)]
    assert hits == []


def test_gitignore_covers_credentials():
    ignore = (ROOT / ".gitignore").read_text()
    for pattern in ("client_secret*.json", "token*.json", "*service_account*.json", ".env", "*.pem"):
        assert pattern in ignore
