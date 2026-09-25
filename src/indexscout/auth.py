"""Read-only Google authentication, secure token storage, and secret redaction.

Only the refresh token and OAuth client identity are persisted. Access tokens live in
memory, so concurrent MCP clients never race to write refreshed tokens; the file lock
only guards login, logout, and reads during a write.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from filelock import FileLock

READONLY_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPES = [READONLY_SCOPE]
KEYRING_SERVICE = "indexscout"
KEYRING_USER = "google-oauth"
FILE_WARNING = (
    "No usable OS keyring was found. The refresh token is stored in a 0600 file instead. "
    "Anyone who can read your user files can read it."
)


class AuthError(RuntimeError):
    """Credentials are missing, unusable, or not read-only."""


# --- redaction ------------------------------------------------------------------------------

_SECRET_PATTERNS = [
    re.compile(r"ya29\.[\w\-.]+"),  # access tokens
    re.compile(r"1//[\w\-.]+"),  # refresh tokens
    re.compile(r"GOCSPX-[\w\-]+"),  # OAuth client secrets
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(
        r"(?i)(\"?(?:access_token|refresh_token|client_secret|private_key|id_token|code)\"?\s*[:=]\s*)"
        r"(\"[^\"]*\"|[^\s&,}]+)"
    ),
]


def redact(text: str) -> str:
    """Remove token-like and credential-like values from text."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda m: m.group(1) + "[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        if record.exc_info:
            # Tracebacks can carry request bodies and response text; keep only the type.
            record.msg += f" ({record.exc_info[0].__name__ if record.exc_info[0] else 'error'})"
            record.exc_info = None
            record.exc_text = None
        return True


def configure_logging() -> None:
    """Log warnings to stderr only (stdout carries MCP), always redacted, never API data."""
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(logging.Formatter("indexscout %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.WARNING)
    # The discovery client and oauthlib log URLs and bodies at lower levels.
    for noisy in ("googleapiclient", "google_auth_oauthlib", "oauthlib", "urllib3", "httplib2", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --- paths ----------------------------------------------------------------------------------


def config_dir() -> Path:
    override = os.environ.get("INDEXSCOUT_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    from platformdirs import user_config_dir

    return Path(user_config_dir("indexscout"))


def client_secrets_path() -> Path:
    override = os.environ.get("INDEXSCOUT_CLIENT_SECRETS")
    return Path(override).expanduser() if override else config_dir() / "client_secret.json"


def service_account_path() -> Path | None:
    value = os.environ.get("INDEXSCOUT_SERVICE_ACCOUNT_FILE")
    return Path(value).expanduser() if value else None


def token_file() -> Path:
    return config_dir() / "token.json"


def _lock() -> FileLock:
    d = config_dir()
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    return FileLock(str(d / "token.lock"), timeout=30)


# --- token storage --------------------------------------------------------------------------


def atomic_write_private(path: Path, data: str) -> None:
    """Write `data` so readers see the old or new file, never a partial one, with mode 0600."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".token-", suffix=".tmp")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def keyring_usable() -> bool:
    choice = os.environ.get("INDEXSCOUT_TOKEN_STORE", "auto").lower()
    if choice == "file":
        return False
    try:
        import keyring
        from keyring.backends import chainer, fail

        kr = keyring.get_keyring()
        usable = not isinstance(kr, fail.Keyring) and float(getattr(kr, "priority", 0)) >= 1
        if isinstance(kr, chainer.ChainerBackend):
            usable = bool(kr.backends)
        if "null" in type(kr).__module__:
            usable = False
    except Exception:
        usable = False
    if choice == "keyring" and not usable:
        raise AuthError("INDEXSCOUT_TOKEN_STORE=keyring is set, but no usable OS keyring was found.")
    return usable


def token_store() -> str:
    return "keyring" if keyring_usable() else "file"


def save_token(info: dict[str, Any]) -> str:
    """Persist the refresh token. Returns the store used ("keyring" or "file")."""
    payload = json.dumps(info)
    with _lock():
        if keyring_usable():
            import keyring

            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, payload)
            with contextlib.suppress(FileNotFoundError):
                token_file().unlink()  # Do not leave an older plaintext copy behind.
            return "keyring"
        atomic_write_private(token_file(), payload)
        return "file"


def load_token() -> dict[str, Any] | None:
    with _lock():
        raw: str | None = None
        if keyring_usable():
            import keyring

            raw = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
        if raw is None and token_file().exists():
            raw = token_file().read_text(encoding="utf-8")
    if raw is None:
        return None
    try:
        info = json.loads(raw)
    except json.JSONDecodeError:
        raise AuthError("Stored token is unreadable. Run `indexscout auth login` again.") from None
    return info if isinstance(info, dict) else None


def delete_token() -> None:
    with _lock():
        if keyring_usable():
            import keyring
            from keyring.errors import PasswordDeleteError

            with contextlib.suppress(PasswordDeleteError):
                keyring.delete_password(KEYRING_SERVICE, KEYRING_USER)
        with contextlib.suppress(FileNotFoundError):
            token_file().unlink()


# --- credentials ----------------------------------------------------------------------------


def auth_method() -> str:
    return "service_account" if service_account_path() else "oauth"


def _require_readonly(scopes: list[str] | None) -> None:
    if not scopes or READONLY_SCOPE not in scopes:
        raise AuthError("Stored credentials do not include the webmasters.readonly scope. Log in again.")


def get_credentials() -> Any:
    """Return read-only Google credentials or raise AuthError with the next action."""
    sa = service_account_path()
    if sa:
        from google.oauth2 import service_account

        if not sa.is_file():
            raise AuthError("INDEXSCOUT_SERVICE_ACCOUNT_FILE does not point to a readable file.")
        return service_account.Credentials.from_service_account_file(str(sa), scopes=SCOPES)  # type: ignore[no-untyped-call]
    info = load_token()
    if not info:
        raise AuthError("Not authenticated. Ask the user to run `indexscout auth login` in a terminal.")
    _require_readonly(info.get("scopes"))
    from google.oauth2.credentials import Credentials

    # Refresh with the read-only scope only; never widen the grant (see mcp-gsc issue #56).
    return Credentials.from_authorized_user_info(info, scopes=SCOPES)  # type: ignore[no-untyped-call]


def login(open_browser: bool = True) -> dict[str, Any]:
    """Run the desktop OAuth flow and store the refresh token. Returns non-secret status."""
    secrets = client_secrets_path()
    if not secrets.is_file():
        raise AuthError(
            f"OAuth client file not found at {secrets}. Create a Desktop OAuth client in Google Cloud, "
            "download its JSON, and save it there or set INDEXSCOUT_CLIENT_SECRETS."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), scopes=SCOPES)
    # The loopback listener binds to localhost on a random port only for this one redirect.
    creds = flow.run_local_server(
        host="localhost",
        port=0,
        open_browser=open_browser,
        authorization_prompt_message="Open this URL to approve read-only Search Console access:\n{url}\n",
        success_message="IndexScout received read-only access. You can close this tab.",
    )
    granted = list(getattr(creds, "granted_scopes", None) or creds.scopes or [])
    _require_readonly(granted)
    info = json.loads(creds.to_json())
    info.pop("token", None)  # Access tokens stay in memory.
    info.pop("expiry", None)
    info["scopes"] = granted
    store = save_token(info)
    return {"authenticated": True, "token_store": store, "scopes": granted}


def status() -> dict[str, Any]:
    """Non-secret authentication status for CLI, doctor, and gsc_capabilities."""
    method = auth_method()
    out: dict[str, Any] = {"method": method, "authenticated": False, "scopes": [], "warnings": []}
    try:
        if method == "service_account":
            sa = service_account_path()
            out["service_account_file_present"] = bool(sa and sa.is_file())
            out["authenticated"] = out["service_account_file_present"]
            out["scopes"] = SCOPES if out["authenticated"] else []
            return out
        out["client_secrets_present"] = client_secrets_path().is_file()
        out["token_store"] = token_store()
        info = load_token()
        if info:
            out["scopes"] = list(info.get("scopes") or [])
            out["authenticated"] = READONLY_SCOPE in out["scopes"]
        if out["token_store"] == "file":  # noqa: S105
            out["warnings"].append(FILE_WARNING)
    except AuthError as exc:
        out["warnings"].append(str(exc))
    return out
