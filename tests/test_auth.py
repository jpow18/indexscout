import json
import logging
import os
import pathlib
import stat
import threading

import pytest

from indexscout import auth


def test_only_readonly_scope_is_requested():
    assert auth.SCOPES == ["https://www.googleapis.com/auth/webmasters.readonly"]
    src = pathlib.Path(auth.__file__).read_text()
    assert '"https://www.googleapis.com/auth/webmasters"' not in src


def test_login_requests_readonly_scope(tmp_path, monkeypatch):
    secrets = tmp_path / "client.json"
    secrets.write_text("{}")
    monkeypatch.setenv("INDEXSCOUT_CLIENT_SECRETS", str(secrets))
    seen = {}

    class FakeCreds:
        granted_scopes = auth.SCOPES
        scopes = auth.SCOPES

        def to_json(self):
            return json.dumps(
                {
                    "token": "ya29.secret",
                    "refresh_token": "1//r",
                    "client_id": "c",
                    "client_secret": "GOCSPX-s",
                    "expiry": "x",
                }
            )

    class FakeFlow:
        @classmethod
        def from_client_secrets_file(cls, path, scopes):
            seen["scopes"] = scopes
            return cls()

        def run_local_server(self, **kw):
            seen["host"] = kw["host"]
            return FakeCreds()

    import google_auth_oauthlib.flow

    monkeypatch.setattr(google_auth_oauthlib.flow, "InstalledAppFlow", FakeFlow)
    out = auth.login(open_browser=False)
    assert seen == {"scopes": auth.SCOPES, "host": "localhost"}
    assert out["token_store"] == "file"
    stored = auth.load_token()
    assert "token" not in stored and "expiry" not in stored  # access tokens stay in memory
    assert stored["scopes"] == auth.SCOPES


def test_login_rejects_missing_readonly_grant(tmp_path, monkeypatch):
    with pytest.raises(auth.AuthError):
        auth._require_readonly(["https://www.googleapis.com/auth/webmasters"])


def test_token_file_is_private_and_atomic():
    auth.save_token({"refresh_token": "1//abc", "scopes": auth.SCOPES})
    path = auth.token_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]
    assert auth.load_token()["refresh_token"] == "1//abc"
    auth.delete_token()
    assert auth.load_token() is None


def test_atomic_write_keeps_old_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / "t.json"
    auth.atomic_write_private(target, "old")
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        auth.atomic_write_private(target, "new")
    assert target.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["t.json"]


def test_concurrent_saves_never_corrupt():
    def worker(i):
        for _ in range(20):
            auth.save_token({"refresh_token": f"1//{i}", "scopes": auth.SCOPES})
            assert auth.load_token()["scopes"] == auth.SCOPES

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    json.loads(auth.token_file().read_text())


def test_unauthenticated_status_and_credentials():
    st = auth.status()
    assert st["authenticated"] is False and st["method"] == "oauth"
    assert auth.FILE_WARNING in st["warnings"]
    with pytest.raises(auth.AuthError, match="auth login"):
        auth.get_credentials()


def test_keyring_forced_but_missing(monkeypatch):
    monkeypatch.setenv("INDEXSCOUT_TOKEN_STORE", "keyring")
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(keyring, "get_keyring", lambda: fail.Keyring())
    with pytest.raises(auth.AuthError):
        auth.keyring_usable()


def test_service_account_uses_readonly_scope(tmp_path, monkeypatch):
    sa = tmp_path / "sa.json"
    sa.write_text("{}")
    monkeypatch.setenv("INDEXSCOUT_SERVICE_ACCOUNT_FILE", str(sa))
    from google.oauth2 import service_account

    seen = {}
    monkeypatch.setattr(
        service_account.Credentials,
        "from_service_account_file",
        classmethod(lambda cls, path, scopes: seen.setdefault("scopes", scopes)),
    )
    auth.get_credentials()
    assert seen["scopes"] == auth.SCOPES
    assert auth.status()["authenticated"] is True


@pytest.mark.parametrize(
    "secret",
    [
        "ya29.a0AfH6SMBx-abc",
        "1//0gLongRefreshToken-x",
        "GOCSPX-AbCdEf123",
        "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----",
    ],
)
def test_redact(secret):
    assert secret not in auth.redact(f"prefix {secret} suffix")


def test_redact_key_value_pairs():
    text = '{"refresh_token": "abc123", "client_secret": "xyz", "code": "4/0Ab"} code=4/0Ab&state=1'
    out = auth.redact(text)
    for s in ("abc123", "xyz", "4/0Ab"):
        assert s not in out


def test_logging_filter_redacts_and_drops_tracebacks(capsys):
    auth.configure_logging()
    log = logging.getLogger("indexscout.test")
    try:
        raise ValueError("body refresh_token=1//leak")
    except ValueError:
        log.warning("failed with token %s", "ya29.leak", exc_info=True)
    err = capsys.readouterr().err
    assert "ya29.leak" not in err and "1//leak" not in err and "Traceback" not in err
