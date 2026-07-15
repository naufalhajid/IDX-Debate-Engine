from __future__ import annotations

import base64
import inspect
import json
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from providers import oauth_manager as oauth


def _jwt_with_exp(exp_seconds: int) -> str:
    def encode(payload: dict[str, object]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'exp': exp_seconds})}.signature"


@pytest.fixture
def oauth_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    token_dir = tmp_path / "tokens"
    monkeypatch.setattr(
        oauth,
        "settings",
        SimpleNamespace(
            TOKEN_STORAGE_DIR=str(token_dir),
            CODEX_OAUTH_CLIENT_ID="test-client",
        ),
    )
    return token_dir


def test_normalise_codex_credential_uses_jwt_exp_and_keeps_lifecycle_fields(
    oauth_store: Path,
) -> None:
    exp_seconds = int(time.time()) + 3600
    credential = oauth.normalise_codex_credential(
        {
            "auth_mode": "chatgpt",
            "tokens": {
                "access_token": _jwt_with_exp(exp_seconds),
                "refresh_token": "refresh-sentinel",
            },
        },
        source="codex_cli",
    )

    assert credential["access_token"]
    assert credential["refresh_token"] == "refresh-sentinel"
    assert credential["expires_in"] > 0
    assert credential["expires_at_ms"] == exp_seconds * 1000
    assert credential["expiry_source"] == "jwt_exp"
    assert credential["credential_type"] == "oauth"
    assert credential["source"] == "codex_cli"


def test_managed_key_without_expiry_is_valid_but_oauth_fails_closed() -> None:
    assert oauth.is_codex_credential_valid(
        {
            "access_token": "sk-managed-sentinel",
            "credential_type": "managed_api_key",
            "expires_at_ms": 0,
        }
    )
    assert not oauth.is_codex_credential_valid(
        {
            "access_token": "opaque-oauth-sentinel",
            "credential_type": "oauth",
            "expires_at_ms": 0,
        }
    )


def test_import_codex_cli_supports_nested_tokens_and_persists_refresh_token(
    oauth_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exp_seconds = int(time.time()) + 3600
    cli_dir = tmp_path / ".codex"
    cli_dir.mkdir()
    (cli_dir / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": _jwt_with_exp(exp_seconds),
                    "refresh_token": "nested-refresh-sentinel",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oauth.Path, "home", lambda: tmp_path)

    imported = oauth.import_codex_cli_tokens()

    assert imported is not None
    assert imported["refresh_token"] == "nested-refresh-sentinel"
    assert imported["expires_at_ms"] == exp_seconds * 1000
    assert oauth._read_auth_store()["codex"]["refresh_token"] == (
        "nested-refresh-sentinel"
    )


def test_expired_oauth_refreshes_once_and_persists_rotated_credentials(
    oauth_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expired = oauth.normalise_codex_credential(
        {
            "access_token": _jwt_with_exp(int(time.time()) - 60),
            "refresh_token": "old-refresh-sentinel",
        },
        source="test",
    )
    oauth._write_auth_store({"codex": expired})
    calls: list[str] = []
    fresh_token = _jwt_with_exp(int(time.time()) + 7200)

    def fake_refresh(refresh_token: str) -> dict[str, object]:
        calls.append(refresh_token)
        return {
            "access_token": fresh_token,
            "refresh_token": "rotated-refresh-sentinel",
            "expires_in": 7200,
        }

    monkeypatch.setattr(oauth, "_refresh_codex_oauth", fake_refresh, raising=False)
    monkeypatch.setattr(
        oauth,
        "import_codex_cli_tokens",
        lambda **_kwargs: pytest.fail("CLI import must not run after refresh success"),
    )
    monkeypatch.setattr(
        oauth.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("refresh helper must own the request"),
    )

    assert oauth.resolve_codex_token() == fresh_token
    assert calls == ["old-refresh-sentinel"]
    stored = oauth._read_auth_store()["codex"]
    assert stored["refresh_token"] == "rotated-refresh-sentinel"
    assert stored["expires_in"] == 7200
    assert stored["credential_type"] == "oauth"


def test_refresh_failure_falls_back_to_cli_import_once(
    oauth_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth._write_auth_store(
        {
            "codex": {
                "access_token": _jwt_with_exp(int(time.time()) - 60),
                "refresh_token": "bad-refresh-sentinel",
                "credential_type": "oauth",
                "expires_at_ms": 0,
            }
        }
    )
    refresh_calls = 0
    import_calls = 0
    imported_token = _jwt_with_exp(int(time.time()) + 3600)

    def fail_refresh(_refresh_token: str) -> dict[str, object]:
        nonlocal refresh_calls
        refresh_calls += 1
        raise RuntimeError("invalid_grant")

    def fake_import(**_kwargs: object) -> dict[str, object]:
        nonlocal import_calls
        import_calls += 1
        return oauth.normalise_codex_credential(
            {
                "access_token": imported_token,
                "refresh_token": "cli-refresh-sentinel",
            },
            source="codex_cli",
        )

    monkeypatch.setattr(oauth, "_refresh_codex_oauth", fail_refresh, raising=False)
    monkeypatch.setattr(oauth, "import_codex_cli_tokens", fake_import)
    monkeypatch.setattr(
        oauth.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("refresh helper must own the request"),
    )

    assert oauth.resolve_codex_token() == imported_token
    assert refresh_calls == 1
    assert import_calls == 1


def test_refresh_and_import_failure_terminate_without_loop(
    oauth_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oauth._write_auth_store(
        {
            "codex": {
                "access_token": "expired-oauth-sentinel",
                "refresh_token": "bad-refresh-sentinel",
                "credential_type": "oauth",
                "expires_at_ms": 1,
            }
        }
    )
    calls = {"refresh": 0, "import": 0}

    def fail_refresh(_refresh_token: str) -> dict[str, object]:
        calls["refresh"] += 1
        raise RuntimeError("refresh failed")

    def fail_import(**_kwargs: object) -> None:
        calls["import"] += 1
        return None

    monkeypatch.setattr(oauth, "_refresh_codex_oauth", fail_refresh, raising=False)
    monkeypatch.setattr(oauth, "import_codex_cli_tokens", fail_import)
    monkeypatch.setattr(
        oauth.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: pytest.fail("refresh helper must own the request"),
    )

    with pytest.raises(ValueError, match="No valid Codex credentials"):
        oauth.resolve_codex_token()

    assert calls == {"refresh": 1, "import": 1}


def test_401_recovery_invalidates_rejected_token_and_refreshes_once(
    oauth_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejected_token = _jwt_with_exp(int(time.time()) + 3600)
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {
                    "access_token": rejected_token,
                    "refresh_token": "recovery-refresh-sentinel",
                },
                source="test",
            )
        }
    )
    recovered_token = _jwt_with_exp(int(time.time()) + 7200)
    refresh_calls = 0

    def fake_refresh(_refresh_token: str) -> dict[str, object]:
        nonlocal refresh_calls
        refresh_calls += 1
        return {
            "access_token": recovered_token,
            "refresh_token": "recovered-refresh-sentinel",
            "expires_in": 7200,
        }

    monkeypatch.setattr(oauth, "_refresh_codex_oauth", fake_refresh, raising=False)
    monkeypatch.setattr(
        oauth,
        "import_codex_cli_tokens",
        lambda **_kwargs: pytest.fail("CLI fallback is not needed"),
    )

    result = oauth.recover_codex_token_after_auth_failure(
        rejected_token_fingerprint=oauth.codex_token_fingerprint(rejected_token)
    )

    assert result == recovered_token
    assert refresh_calls == 1
    assert oauth._read_auth_store()["codex"]["access_token"] == recovered_token


def test_same_rejected_cli_access_can_supply_missing_refresh_token(
    oauth_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rejected_token = _jwt_with_exp(int(time.time()) + 3600)
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {"access_token": rejected_token, "refresh_token": ""},
                source="legacy_store",
            )
        }
    )
    cli_dir = tmp_path / ".codex"
    cli_dir.mkdir()
    (cli_dir / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": rejected_token,
                    "refresh_token": "cli-refresh-sentinel",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oauth.Path, "home", lambda: tmp_path)
    refreshed_token = _jwt_with_exp(int(time.time()) + 7200)
    refresh_calls: list[str] = []

    def fake_refresh(refresh_token: str) -> dict[str, object]:
        refresh_calls.append(refresh_token)
        return {
            "access_token": refreshed_token,
            "refresh_token": "rotated-cli-refresh",
            "expires_in": 7200,
        }

    monkeypatch.setattr(oauth, "_refresh_codex_oauth", fake_refresh)

    result = oauth.recover_codex_token_after_auth_failure(
        rejected_token_fingerprint=oauth.codex_token_fingerprint(rejected_token)
    )

    assert result == refreshed_token
    assert refresh_calls == ["cli-refresh-sentinel"]
    assert oauth._read_auth_store()["codex"]["refresh_token"] == (
        "rotated-cli-refresh"
    )


def test_valid_legacy_token_is_enriched_from_codex_cli(
    oauth_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = _jwt_with_exp(int(time.time()) + 3600)
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {"access_token": access_token, "refresh_token": ""},
                source="legacy_store",
            )
        }
    )
    cli_dir = tmp_path / ".codex"
    cli_dir.mkdir()
    (cli_dir / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access_token,
                    "refresh_token": "enrichment-refresh-sentinel",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oauth.Path, "home", lambda: tmp_path)

    assert oauth.resolve_codex_token() == access_token
    assert oauth._read_auth_store()["codex"]["refresh_token"] == (
        "enrichment-refresh-sentinel"
    )


def test_resolve_does_not_rewrite_already_normalized_store(
    oauth_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential = oauth.normalise_codex_credential(
        {
            "access_token": _jwt_with_exp(int(time.time()) + 3600),
            "refresh_token": "refresh-present",
        },
        source="test",
    )
    oauth._write_auth_store({"codex": credential})
    original_write = oauth._write_auth_store
    writes = 0

    def counting_write(data: dict[str, object]) -> None:
        nonlocal writes
        writes += 1
        original_write(data)

    monkeypatch.setattr(oauth, "_write_auth_store", counting_write)
    monkeypatch.setattr(
        oauth,
        "import_codex_cli_tokens",
        lambda **_kwargs: pytest.fail("normalized credential needs no import"),
    )

    oauth.resolve_codex_token()
    oauth.resolve_codex_token()

    assert writes == 0


def test_compare_and_swap_preserves_concurrently_rotated_token(
    oauth_store: Path,
) -> None:
    rejected = _jwt_with_exp(int(time.time()) + 1800)
    rotated = _jwt_with_exp(int(time.time()) + 7200)
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {
                    "access_token": rotated,
                    "refresh_token": "rotated-refresh",
                },
                source="concurrent_refresh",
            )
        }
    )

    recovered = oauth.recover_codex_token_after_auth_failure(
        rejected_token_fingerprint=oauth.codex_token_fingerprint(rejected)
    )

    assert recovered == rotated
    assert oauth._read_auth_store()["codex"]["access_token"] == rotated


def test_auth_add_persists_complete_device_credential(
    oauth_store: Path,
) -> None:
    from app.cli.commands import auth as auth_command

    token = _jwt_with_exp(int(time.time()) + 3600)
    auth_command._add_token(
        "codex",
        {
            "access_token": token,
            "refresh_token": "device-refresh-sentinel",
            "expires_in": 3600,
            "auth_mode": "chatgpt",
        },
        source="device_code",
    )

    stored = oauth._read_auth_store()["codex"]
    assert stored["access_token"] == token
    assert stored["refresh_token"] == "device-refresh-sentinel"
    assert stored["expires_in"] > 0
    assert stored["expires_at_ms"] > int(time.time() * 1000)
    assert stored["credential_type"] == "oauth"


def test_secret_redaction_removes_bearer_access_and_refresh_tokens() -> None:
    from utils.secret_redaction import redact_secrets

    raw = (
        "Authorization: Bearer access-sentinel "
        "access_token=access-sentinel refresh_token=refresh-sentinel"
    )
    redacted = redact_secrets(raw)

    assert "access-sentinel" not in redacted
    assert "refresh-sentinel" not in redacted
    assert "[REDACTED]" in redacted


def test_oauth_refresh_failure_log_redacts_actual_credentials(
    oauth_store: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = "expired-access-sentinel"
    refresh_token = "refresh-token-sentinel"
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "expires_at_ms": int(time.time() * 1000) - 60_000,
                    "credential_type": "oauth",
                },
                source="test",
            )
        }
    )
    captured: list[str] = []

    def fail_refresh(_refresh_token: str) -> dict[str, object]:
        raise RuntimeError(
            f"Authorization: Bearer {access_token} "
            f"refresh_token={refresh_token}"
        )

    monkeypatch.setattr(oauth, "_refresh_codex_oauth", fail_refresh)
    monkeypatch.setattr(oauth, "import_codex_cli_tokens", lambda **_kwargs: None)
    monkeypatch.setattr(
        oauth.logger,
        "warning",
        lambda message, *args: captured.append(str(message).format(*args)),
    )

    with pytest.raises(ValueError, match="No valid Codex credentials"):
        oauth.resolve_codex_token()

    rendered = "\n".join(captured)
    assert access_token not in rendered
    assert refresh_token not in rendered
    assert "[REDACTED]" in rendered


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_windows_auth_store_is_encrypted_at_rest_with_dpapi(
    oauth_store: Path,
) -> None:
    access_token = "dpapi-access-token-sentinel"
    refresh_token = "dpapi-refresh-token-sentinel"
    oauth._write_auth_store(
        {
            "codex": {
                "access_token": access_token,
                "refresh_token": refresh_token,
            }
        }
    )

    raw_text = oauth._auth_json_path().read_text(encoding="utf-8")
    envelope = json.loads(raw_text)

    assert envelope["format"] == oauth._DPAPI_AUTH_FORMAT
    assert access_token not in raw_text
    assert refresh_token not in raw_text
    assert oauth._read_auth_store()["codex"]["access_token"] == access_token
    assert oauth._read_auth_store()["codex"]["refresh_token"] == refresh_token


def test_cli_import_compare_and_swap_preserves_concurrent_rotation(
    oauth_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_token = _jwt_with_exp(int(time.time()) + 1800)
    cli_token = _jwt_with_exp(int(time.time()) + 3600)
    rotated_token = _jwt_with_exp(int(time.time()) + 7200)
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {
                    "access_token": old_token,
                    "refresh_token": "old-refresh",
                },
                source="old",
            )
        }
    )
    cli_dir = tmp_path / ".codex"
    cli_dir.mkdir()
    (cli_dir / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": cli_token,
                    "refresh_token": "cli-refresh",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oauth.Path, "home", lambda: tmp_path)
    original_persist = oauth._persist_codex_credential
    rotated_credential = oauth.normalise_codex_credential(
        {
            "access_token": rotated_token,
            "refresh_token": "rotated-refresh",
        },
        source="concurrent_refresh",
    )
    injected = False

    def race_persist(credential, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            oauth._write_auth_store({"codex": rotated_credential})
        return original_persist(credential, **kwargs)

    monkeypatch.setattr(oauth, "_persist_codex_credential", race_persist)

    resolved = oauth.import_codex_cli_tokens()

    assert resolved is not None
    assert resolved["access_token"] == rotated_token
    assert oauth._read_auth_store()["codex"]["access_token"] == rotated_token


def test_device_login_uses_same_configured_client_id_as_refresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.cli.commands import auth as auth_command

    requests = []
    monkeypatch.setattr(
        auth_command,
        "settings",
        SimpleNamespace(CODEX_OAUTH_CLIENT_ID="configured-client-id"),
    )

    def fail_after_capture(request, **_kwargs):
        requests.append(request)
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(auth_command.urllib.request, "urlopen", fail_after_capture)

    assert auth_command._codex_device_code_login() is None
    assert json.loads(requests[0].data.decode("utf-8"))["client_id"] == (
        "configured-client-id"
    )


def test_auth_add_command_has_no_secret_command_line_parameter() -> None:
    from app.cli.commands.auth import auth_add_command

    parameters = list(inspect.signature(auth_add_command).parameters)
    assert parameters == ["provider", "managed_key"]
    assert "token" not in parameters


@pytest.mark.parametrize("blank_access", [False, True])
def test_cli_import_cas_detects_refresh_only_rotation(
    oauth_store: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blank_access: bool,
) -> None:
    shared_access = _jwt_with_exp(int(time.time()) + 3600)
    local_access = "" if blank_access else shared_access
    oauth._write_auth_store(
        {
            "codex": oauth.normalise_codex_credential(
                {
                    "access_token": local_access,
                    "refresh_token": "old-refresh",
                    "credential_type": "oauth",
                },
                source="old",
            )
        }
    )
    cli_dir = tmp_path / ".codex"
    cli_dir.mkdir()
    (cli_dir / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": shared_access,
                    "refresh_token": "cli-refresh",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(oauth.Path, "home", lambda: tmp_path)
    original_persist = oauth._persist_codex_credential
    rotated = oauth.normalise_codex_credential(
        {
            "access_token": local_access,
            "refresh_token": "rotated-refresh",
            "credential_type": "oauth",
        },
        source="concurrent_refresh",
    )
    injected = False

    def race_persist(credential, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            oauth._write_auth_store({"codex": rotated})
        return original_persist(credential, **kwargs)

    monkeypatch.setattr(oauth, "_persist_codex_credential", race_persist)

    result = oauth.import_codex_cli_tokens()

    assert result is not None
    assert result["refresh_token"] == "rotated-refresh"
    assert oauth._read_auth_store()["codex"]["refresh_token"] == (
        "rotated-refresh"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI only")
def test_resolve_migrates_normalized_plaintext_store_to_dpapi(
    oauth_store: Path,
) -> None:
    token = _jwt_with_exp(int(time.time()) + 3600)
    credential = oauth.normalise_codex_credential(
        {
            "access_token": token,
            "refresh_token": "migration-refresh",
        },
        source="legacy_plaintext",
    )
    path = oauth._auth_json_path()
    path.write_text(json.dumps({"codex": credential}), encoding="utf-8")

    assert oauth.resolve_codex_token() == token

    raw_text = path.read_text(encoding="utf-8")
    assert json.loads(raw_text)["format"] == oauth._DPAPI_AUTH_FORMAT
    assert token not in raw_text
    assert oauth._read_auth_store()["codex"]["refresh_token"] == (
        "migration-refresh"
    )


def test_managed_codex_key_uses_hidden_prompt_not_device_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import getpass

    from app.cli.commands import auth as auth_command

    captured: dict[str, object] = {}
    monkeypatch.setattr(getpass, "getpass", lambda _prompt: "managed-secret")
    monkeypatch.setattr(
        auth_command,
        "_codex_device_code_login",
        lambda: pytest.fail("managed key mode must not start device flow"),
    )
    monkeypatch.setattr(
        auth_command,
        "_add_token",
        lambda provider, credential, *, source: captured.update(
            provider=provider,
            credential=credential,
            source=source,
        ),
    )

    auth_command.auth_add_command("codex", managed_key=True)

    assert captured["provider"] == "codex"
    assert captured["source"] == "managed_key_prompt"
    assert captured["credential"] == {
        "access_token": "managed-secret",
        "credential_type": "managed_api_key",
        "auth_mode": "managed_api_key",
    }
