"""providers/oauth_manager.py — Token storage & credential resolution (Hermes spec).

Replicates the exact authentication flows from hermes-agent's auth module:
1. File-based mutex lock for concurrent token refresh safety.
2. OpenAI Codex Device Code Flow (client_id = app_EMoamEEZ73f0CkXaXp7hrann).
3. Codex CLI auto-import (~/.codex/auth.json).
4. Anthropic OAuth token resolution (env var → keychain → credentials file → claude.json).
5. Token persistence to output/tokens/auth.json with owner-only permissions (0o600).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import secrets
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.settings import settings
from utils.logger_config import logger
from utils.secret_redaction import redact_secrets

# ---------------------------------------------------------------------------
# File-based mutex lock (Hermes spec: auth.json.lock)
# ---------------------------------------------------------------------------


@contextmanager
def file_lock(lock_path: str, *, timeout: float = 30.0) -> Iterator[None]:
    """Cross-platform file lock using 1-byte locking.

    Uses msvcrt.locking on Windows and fcntl.flock on Unix.
    Prevents concurrent processes from refreshing the same token simultaneously.
    """
    lock_file = None
    start = time.monotonic()
    try:
        os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
        lock_file = open(lock_path, "wb")

        while True:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (OSError, IOError):
                if time.monotonic() - start > timeout:
                    raise TimeoutError(
                        f"Could not acquire file lock {lock_path} within {timeout}s"
                    )
                time.sleep(0.1)

        yield

    finally:
        if lock_file is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt

                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()


# ---------------------------------------------------------------------------
# Token persistence (output/tokens/auth.json)
# ---------------------------------------------------------------------------

_DPAPI_AUTH_FORMAT = "idx-auth-dpapi-v1"
_DPAPI_ENTROPY = b"idx-fundamental-analysis/auth-store/v1"


def _windows_dpapi_transform(payload: bytes, *, protect: bool) -> bytes:
    """Protect or unprotect bytes with the current Windows user via DPAPI."""
    if sys.platform != "win32":
        return payload

    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    def make_blob(data: bytes) -> tuple[DataBlob, Any]:
        buffer = ctypes.create_string_buffer(data)
        blob = DataBlob(
            len(data),
            ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
        )
        return blob, buffer

    input_blob, input_buffer = make_blob(payload)
    entropy_blob, entropy_buffer = make_blob(_DPAPI_ENTROPY)
    output_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN

    if protect:
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(DataBlob),
            wintypes.LPCWSTR,
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        crypt32.CryptProtectData.restype = wintypes.BOOL
        success = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "IDX Fundamental Analysis credentials",
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(DataBlob),
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = wintypes.BOOL
        success = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )

    # Keep buffers alive until the Win32 call has completed.
    _ = input_buffer, entropy_buffer
    if not success:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(output_blob.pbData)


def _token_storage_dir() -> Path:
    """Resolve the token storage directory (settings.TOKEN_STORAGE_DIR)."""
    p = Path(settings.TOKEN_STORAGE_DIR)
    if not p.is_absolute():
        from core.settings import ROOT_PATH

        p = ROOT_PATH / p
    p.mkdir(parents=True, exist_ok=True)
    return p


def _auth_json_path() -> Path:
    return _token_storage_dir() / "auth.json"


def _lock_path() -> str:
    return str(_token_storage_dir() / "auth.json.lock")


def _read_auth_store() -> dict[str, Any]:
    """Read the local auth.json token store."""
    path = _auth_json_path()
    if not path.exists():
        return {}
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(stored, dict) and stored.get("format") == _DPAPI_AUTH_FORMAT:
            encrypted = base64.b64decode(str(stored.get("payload") or ""))
            decrypted = _windows_dpapi_transform(encrypted, protect=False)
            decoded = json.loads(decrypted.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("Decrypted auth store must contain a JSON object")
            return decoded
        if not isinstance(stored, dict):
            raise ValueError("Auth store must contain a JSON object")
        return stored
    except (OSError, UnicodeError, ValueError) as exc:
        logger.warning(
            "[OAuth] auth.json unreadable ({}); treating as empty credential store.",
            exc,
        )
        return {}


def _auth_store_uses_dpapi() -> bool:
    if sys.platform != "win32":
        return False
    path = _auth_json_path()
    if not path.exists():
        return False
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(stored, dict) and stored.get("format") == _DPAPI_AUTH_FORMAT


def _write_auth_store(data: dict[str, Any]) -> None:
    """Write tokens to auth.json with owner-only permissions (0o600).

    Uses atomic temp-file + os.replace pattern (Hermes spec).
    """
    path = _auth_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}.{secrets.token_hex(4)}")
    try:
        fd = os.open(
            str(tmp),
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        serialized = json.dumps(data, indent=2)
        if sys.platform == "win32":
            encrypted = _windows_dpapi_transform(
                serialized.encode("utf-8"),
                protect=True,
            )
            serialized = json.dumps(
                {
                    "format": _DPAPI_AUTH_FORMAT,
                    "payload": base64.b64encode(encrypted).decode("ascii"),
                },
                indent=2,
            )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _now_ms() -> int:
    return int(time.time() * 1000)


def _positive_int(value: Any) -> int:
    try:
        converted = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return 0
    return converted if converted > 0 else 0


def _jwt_exp_ms(access_token: str) -> int:
    """Read an unverified JWT exp claim for expiry metadata only."""
    try:
        parts = str(access_token or "").split(".")
        if len(parts) != 3:
            return 0
        payload = parts[1] + ("=" * (-len(parts[1]) % 4))
        decoded = base64.urlsafe_b64decode(payload.encode("ascii"))
        exp_seconds = _positive_int(json.loads(decoded.decode("utf-8")).get("exp"))
        return exp_seconds * 1000 if exp_seconds else 0
    except (ValueError, UnicodeError, json.JSONDecodeError):
        return 0


def codex_token_fingerprint(access_token: str) -> str:
    """Return a non-reversible token identity suitable for compare-and-swap."""
    value = str(access_token or "")
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


def codex_credential_revision(credential: dict[str, Any]) -> str:
    """Hash all lifecycle fields needed for credential compare-and-swap."""
    material = {
        "access_token": str(credential.get("access_token") or ""),
        "refresh_token": str(credential.get("refresh_token") or ""),
        "expires_at_ms": _positive_int(credential.get("expires_at_ms")),
        "credential_type": str(credential.get("credential_type") or ""),
        "auth_mode": str(credential.get("auth_mode") or ""),
    }
    encoded = json.dumps(
        material,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CodexAuthRecoveryExhausted(RuntimeError):
    """Raised after one bounded Codex credential recovery and replay."""


class CodexCredentialChanged(RuntimeError):
    """Raised when compare-and-swap prevents stale credential persistence."""


def normalise_codex_credential(
    payload: dict[str, Any],
    *,
    source: str,
    auth_mode: str | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Normalize device, CLI, refresh, legacy, and managed-key credentials."""
    raw = payload if isinstance(payload, dict) else {}
    nested = raw.get("tokens")
    tokens = nested if isinstance(nested, dict) else {}
    current_ms = int(now_ms if now_ms is not None else _now_ms())

    access_token = str(
        tokens.get("access_token")
        or tokens.get("token")
        or raw.get("access_token")
        or raw.get("token")
        or raw.get("OPENAI_API_KEY")
        or ""
    )
    refresh_token = str(
        tokens.get("refresh_token") or raw.get("refresh_token") or ""
    )
    resolved_auth_mode = str(
        auth_mode or raw.get("auth_mode") or raw.get("authMode") or ""
    ).strip()

    explicit_expiry = _positive_int(
        tokens.get("expires_at_ms") or raw.get("expires_at_ms")
    )
    if not explicit_expiry:
        explicit_expiry = _positive_int(
            tokens.get("expires_at") or raw.get("expires_at")
        )
        if 0 < explicit_expiry < 1_000_000_000_000:
            explicit_expiry *= 1000

    jwt_expiry = _jwt_exp_ms(access_token)
    supplied_expires_in = _positive_int(
        tokens.get("expires_in") or raw.get("expires_in")
    )
    if explicit_expiry:
        expires_at_ms = explicit_expiry
        expiry_source = str(raw.get("expiry_source") or "explicit")
    elif jwt_expiry:
        expires_at_ms = jwt_expiry
        expiry_source = "jwt_exp"
    elif supplied_expires_in:
        expires_at_ms = current_ms + supplied_expires_in * 1000
        expiry_source = "expires_in"
    else:
        expires_at_ms = 0
        expiry_source = "none"

    declared_type = str(
        raw.get("credential_type") or raw.get("credential_kind") or ""
    ).strip().lower()
    managed_auth_modes = {"api_key", "apikey", "managed_api_key"}
    if declared_type in {"oauth", "managed_api_key"}:
        credential_type = declared_type
    elif resolved_auth_mode.lower() in managed_auth_modes:
        credential_type = "managed_api_key"
    elif (
        access_token.startswith("sk-")
        and not refresh_token
        and not jwt_expiry
    ):
        credential_type = "managed_api_key"
    else:
        credential_type = "oauth"

    if credential_type == "managed_api_key" and not expires_at_ms:
        expiry_source = "managed"

    expires_in = supplied_expires_in
    if not expires_in and expires_at_ms > current_ms:
        expires_in = max(1, (expires_at_ms - current_ms) // 1000)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": int(expires_in),
        "expires_at_ms": int(expires_at_ms),
        "credential_type": credential_type,
        "source": str(source or raw.get("source") or "unknown"),
        "auth_mode": resolved_auth_mode,
        "expiry_source": expiry_source,
        "updated_at_ms": _positive_int(raw.get("updated_at_ms")) or current_ms,
    }


def is_codex_credential_valid(credential: dict[str, Any]) -> bool:
    """Validate Codex credential semantics without treating OAuth as permanent."""
    normalized = normalise_codex_credential(
        credential,
        source=str(credential.get("source") or "auth_store"),
    )
    if not normalized["access_token"]:
        return False
    if normalized["credential_type"] == "managed_api_key":
        expires_at_ms = normalized["expires_at_ms"]
        return not expires_at_ms or is_token_valid(expires_at_ms)
    expires_at_ms = normalized["expires_at_ms"]
    return bool(expires_at_ms and is_token_valid(expires_at_ms))


def get_codex_credential_type(access_token: str) -> str:
    """Resolve transport semantics for one token without exposing the token."""
    fingerprint = codex_token_fingerprint(access_token)
    if fingerprint:
        with file_lock(_lock_path()):
            store = _read_auth_store()
            raw = store.get("codex")
            raw = raw if isinstance(raw, dict) else {}
            credential = normalise_codex_credential(
                raw,
                source=str(raw.get("source") or "auth_store"),
            )
        if codex_token_fingerprint(credential["access_token"]) == fingerprint:
            return str(credential["credential_type"])
    return "managed_api_key" if str(access_token or "").startswith("sk-") else "oauth"


def _persist_codex_credential(
    credential: dict[str, Any],
    *,
    expected_current_fingerprint: str | None = None,
    expected_current_revision: str | None = None,
) -> dict[str, Any]:
    normalized = normalise_codex_credential(
        credential,
        source=str(credential.get("source") or "auth_store"),
    )
    with file_lock(_lock_path()):
        store = _read_auth_store()
        raw_current = store.get("codex")
        raw_current = raw_current if isinstance(raw_current, dict) else {}
        current = normalise_codex_credential(
            raw_current,
            source=str(raw_current.get("source") or "auth_store"),
        )
        if (
            expected_current_fingerprint is not None
            and codex_token_fingerprint(current["access_token"])
            != expected_current_fingerprint
        ):
            raise CodexCredentialChanged(
                "Codex credential changed before compare-and-swap persistence."
            )
        if (
            expected_current_revision is not None
            and codex_credential_revision(current) != expected_current_revision
        ):
            raise CodexCredentialChanged(
                "Codex credential lifecycle changed before compare-and-swap "
                "persistence."
            )
        store["codex"] = normalized
        _write_auth_store(store)
    return normalized


def _read_normalized_stored_codex_credential() -> dict[str, Any]:
    with file_lock(_lock_path()):
        store = _read_auth_store()
        raw = store.get("codex")
        raw = raw if isinstance(raw, dict) else {}
        return normalise_codex_credential(
            raw,
            source=str(raw.get("source") or "auth_store"),
        )


# ---------------------------------------------------------------------------
# Codex CLI auto-import (~/.codex/auth.json)
# ---------------------------------------------------------------------------


def import_codex_cli_tokens(
    *,
    rejected_token_fingerprint: str | None = None,
    expected_token_fingerprint: str | None = None,
    expected_current_fingerprint: str | None = None,
    expected_current_revision: str | None = None,
) -> dict[str, Any] | None:
    """Scan ~/.codex/auth.json for existing Codex CLI tokens.

    Returns token dict or None if not found / invalid.
    """
    codex_auth = Path.home() / ".codex" / "auth.json"
    if not codex_auth.exists():
        return None

    if (
        expected_current_fingerprint is None
        or expected_current_revision is None
    ):
        current = _read_normalized_stored_codex_credential()
        if expected_current_fingerprint is None:
            expected_current_fingerprint = codex_token_fingerprint(
                current["access_token"]
            )
        if expected_current_revision is None:
            expected_current_revision = codex_credential_revision(current)

    try:
        data = json.loads(codex_auth.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        logger.warning(
            "Codex CLI credential import failed: {}",
            redact_secrets(exc),
        )
        return None

    token_data = normalise_codex_credential(
        data,
        source="codex_cli",
        auth_mode=str(data.get("auth_mode") or ""),
    )
    if not token_data["access_token"]:
        return None
    imported_fingerprint = codex_token_fingerprint(token_data["access_token"])
    if expected_token_fingerprint and imported_fingerprint != expected_token_fingerprint:
        return None
    if (
        rejected_token_fingerprint
        and imported_fingerprint == rejected_token_fingerprint
    ):
        if token_data["refresh_token"]:
            token_data["access_token"] = ""
            token_data["expires_in"] = 0
            token_data["expires_at_ms"] = 0
            try:
                token_data = _persist_codex_credential(
                    token_data,
                    expected_current_fingerprint=expected_current_fingerprint,
                    expected_current_revision=expected_current_revision,
                )
            except CodexCredentialChanged:
                logger.debug(
                    "Skipped stale Codex CLI persistence because the local "
                    "credential changed concurrently."
                )
                return _read_normalized_stored_codex_credential()
            logger.debug(
                "Imported a Codex CLI refresh credential after its access "
                "token was rejected."
            )
            return token_data
        logger.warning(
            "Codex CLI credential rejected: imported token matches the "
            "server-rejected credential."
        )
        return None
    if not is_codex_credential_valid(token_data) and not token_data["refresh_token"]:
        logger.warning("Codex CLI credential is expired and not refreshable.")
        return None

    try:
        _persist_codex_credential(
            token_data,
            expected_current_fingerprint=expected_current_fingerprint,
            expected_current_revision=expected_current_revision,
        )
    except CodexCredentialChanged:
        logger.debug(
            "Skipped stale Codex CLI persistence because the local credential "
            "changed concurrently."
        )
        return _read_normalized_stored_codex_credential()

    logger.debug("Imported Codex CLI tokens from ~/.codex/auth.json")
    return token_data


# ---------------------------------------------------------------------------
# Anthropic credential resolution (Hermes spec)
# ---------------------------------------------------------------------------

_CLAUDE_CODE_VERSION_FALLBACK = "2.1.74"
_claude_code_version_cache: str | None = None


def detect_claude_code_version() -> str:
    """Detect installed Claude Code version, fall back to static constant.

    Anthropic's OAuth infrastructure validates user-agent version and may
    reject requests with a version that's too old.
    """
    global _claude_code_version_cache
    if _claude_code_version_cache is not None:
        return _claude_code_version_cache

    for cmd in ("claude", "claude-code"):
        try:
            result = subprocess.run(
                [cmd, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                version = result.stdout.strip().split()[0]
                if version and version[0].isdigit():
                    _claude_code_version_cache = version
                    return version
        except Exception:
            pass

    _claude_code_version_cache = _CLAUDE_CODE_VERSION_FALLBACK
    return _CLAUDE_CODE_VERSION_FALLBACK


def _read_claude_code_credentials_from_keychain() -> dict[str, Any] | None:
    """Read Claude Code OAuth credentials from macOS Keychain.

    Claude Code >=2.1.114 stores credentials in the macOS Keychain under
    "Claude Code-credentials" service name.
    """
    if platform.system() != "Darwin":
        return None

    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                "Claude Code-credentials",
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0 or not result.stdout.strip():
        return None

    data = json.loads(result.stdout.strip())
    oauth_data = data.get("claudeAiOauth")
    if oauth_data and isinstance(oauth_data, dict):
        access_token = oauth_data.get("accessToken", "")
        if access_token:
            return {
                "accessToken": access_token,
                "refreshToken": oauth_data.get("refreshToken", ""),
                "expiresAt": oauth_data.get("expiresAt", 0),
                "source": "macos_keychain",
            }
    return None


def read_claude_code_credentials() -> dict[str, Any] | None:
    """Read refreshable Claude Code OAuth credentials.

    Checks sources in order:
      1. macOS Keychain (Darwin only)
      2. ~/.claude/.credentials.json
    """
    kc_creds = _read_claude_code_credentials_from_keychain()
    if kc_creds:
        return kc_creds

    cred_path = Path.home() / ".claude" / ".credentials.json"
    if cred_path.exists():
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        oauth_data = data.get("claudeAiOauth")
        if oauth_data and isinstance(oauth_data, dict):
            access_token = oauth_data.get("accessToken", "")
            if access_token:
                return {
                    "accessToken": access_token,
                    "refreshToken": oauth_data.get("refreshToken", ""),
                    "expiresAt": oauth_data.get("expiresAt", 0),
                    "source": "claude_code_credentials_file",
                }
    return None


def is_token_valid(expires_at_ms: int | float) -> bool:
    """Check if a token with the given expiry (ms since epoch) is still valid.

    Allows 60 seconds of buffer before actual expiry.
    """
    if not expires_at_ms:
        return True  # No expiry set = valid (managed keys)
    now_ms = int(time.time() * 1000)
    return now_ms < (int(expires_at_ms) - 60_000)


def is_codex_auth_expiry_error(exc: Exception) -> bool:
    """Return True only for server auth failures eligible for one recovery."""
    visited: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        status_candidates = (
            getattr(current, "status_code", None),
            getattr(current, "code", None),
            getattr(getattr(current, "response", None), "status_code", None),
        )
        for status in status_candidates:
            try:
                if int(status) == 401:
                    return True
            except (TypeError, ValueError):
                continue
        message = str(current).lower()
        if (
            "token_expired" in message
            or "token expired" in message
            or "401 unauthorized" in message
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _refresh_codex_oauth(refresh_token: str) -> dict[str, Any]:
    """Exchange one Codex refresh token without retrying or logging secrets."""
    if not refresh_token:
        raise ValueError("refresh_token is required")
    data = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.CODEX_OAUTH_CLIENT_ID,
        }
    ).encode()
    request = urllib.request.Request(
        "https://auth.openai.com/oauth/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.loads(response.read().decode())
    if not result.get("access_token"):
        raise ValueError("Codex refresh response was missing access_token")
    return result


def _refresh_stored_codex_credential(
    *,
    rejected_token_fingerprint: str | None = None,
) -> tuple[str | None, bool]:
    """Refresh the stored credential at most once under the cross-process lock."""
    with file_lock(_lock_path()):
        store = _read_auth_store()
        raw = store.get("codex")
        raw = raw if isinstance(raw, dict) else {}
        credential = normalise_codex_credential(
            raw,
            source=str(raw.get("source") or "auth_store"),
        )
        current_token = credential["access_token"]
        current_fingerprint = codex_token_fingerprint(current_token)
        if (
            current_token
            and rejected_token_fingerprint
            and current_fingerprint != rejected_token_fingerprint
            and is_codex_credential_valid(credential)
        ):
            return current_token, False
        if (
            current_token
            and not rejected_token_fingerprint
            and is_codex_credential_valid(credential)
        ):
            return current_token, False

        refresh_token = credential["refresh_token"]
        if not refresh_token:
            return None, False
        try:
            refreshed_raw = dict(_refresh_codex_oauth(refresh_token))
        except Exception as exc:
            logger.warning(
                "Codex token refresh failed: {}",
                redact_secrets(exc),
            )
            return None, True

        refreshed_raw["refresh_token"] = (
            refreshed_raw.get("refresh_token") or refresh_token
        )
        if (
            not refreshed_raw.get("expires_in")
            and not _jwt_exp_ms(str(refreshed_raw.get("access_token") or ""))
        ):
            refreshed_raw["expires_in"] = 3600
        refreshed_raw["auth_mode"] = credential.get("auth_mode") or "chatgpt"
        refreshed = normalise_codex_credential(
            refreshed_raw,
            source="refresh",
        )
        if not is_codex_credential_valid(refreshed):
            logger.warning("Codex refresh returned an already-expired credential.")
            return None, True
        store["codex"] = refreshed
        _write_auth_store(store)
        logger.debug("Codex token refreshed successfully.")
        return refreshed["access_token"], True


def invalidate_codex_credential(
    *,
    rejected_token_fingerprint: str | None = None,
    reason: str = "server_rejected",
) -> bool:
    """Invalidate a rejected access token while retaining its refresh token."""
    with file_lock(_lock_path()):
        store = _read_auth_store()
        raw = store.get("codex")
        if not isinstance(raw, dict):
            return False
        credential = normalise_codex_credential(
            raw,
            source=str(raw.get("source") or "auth_store"),
        )
        current_fingerprint = codex_token_fingerprint(credential["access_token"])
        if (
            rejected_token_fingerprint
            and current_fingerprint
            and current_fingerprint != rejected_token_fingerprint
        ):
            return False
        credential["access_token"] = ""
        credential["expires_in"] = 0
        credential["expires_at_ms"] = 0
        credential["expiry_source"] = "invalidated"
        credential["invalidated_at_ms"] = _now_ms()
        credential["invalidation_reason"] = str(reason)
        store["codex"] = credential
        _write_auth_store(store)
        return True


def recover_codex_token_after_auth_failure(
    *,
    rejected_token_fingerprint: str | None = None,
) -> str:
    """Invalidate, refresh once, import CLI once, then fail closed."""
    invalidate_codex_credential(
        rejected_token_fingerprint=rejected_token_fingerprint,
        reason="http_401_token_expired",
    )
    refreshed, refresh_attempted = _refresh_stored_codex_credential(
        rejected_token_fingerprint=rejected_token_fingerprint,
    )
    if refreshed:
        return refreshed

    imported = import_codex_cli_tokens(
        rejected_token_fingerprint=rejected_token_fingerprint,
    )
    if imported and is_codex_credential_valid(imported):
        return str(imported["access_token"])
    if imported and imported.get("refresh_token") and not refresh_attempted:
        refreshed, _ = _refresh_stored_codex_credential()
        if refreshed:
            return refreshed
    raise ValueError(
        "Codex credential recovery failed after one refresh and one CLI import."
    )


def refresh_anthropic_oauth_pure(
    refresh_token: str,
    *,
    use_json: bool = False,
) -> dict[str, Any]:
    """Refresh an Anthropic OAuth token without mutating local credential files.

    Hermes spec: tries both platform.claude.com and console.anthropic.com endpoints.
    """
    if not refresh_token:
        raise ValueError("refresh_token is required")

    client_id = settings.ANTHROPIC_OAUTH_CLIENT_ID
    if use_json:
        data = json.dumps(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        ).encode()
        content_type = "application/json"
    else:
        data = urllib.parse.urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            }
        ).encode()
        content_type = "application/x-www-form-urlencoded"

    token_endpoints = [
        "https://platform.claude.com/v1/oauth/token",
        "https://console.anthropic.com/v1/oauth/token",
    ]
    last_error: Exception | None = None
    for endpoint in token_endpoints:
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={
                "Content-Type": content_type,
                "User-Agent": f"claude-cli/{detect_claude_code_version()} (external, cli)",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
        except Exception as exc:
            last_error = exc
            logger.debug(
                "Anthropic token refresh failed at {}: {}",
                endpoint,
                redact_secrets(exc),
            )
            continue

        access_token = result.get("access_token", "")
        if not access_token:
            raise ValueError("Anthropic refresh response was missing access_token")
        next_refresh = result.get("refresh_token", refresh_token)
        expires_in = result.get("expires_in", 3600)
        return {
            "access_token": access_token,
            "refresh_token": next_refresh,
            "expires_at_ms": int(time.time() * 1000) + (expires_in * 1000),
        }

    if last_error is not None:
        raise last_error
    raise ValueError("Anthropic token refresh failed")


# ---------------------------------------------------------------------------
# Unified credential resolution
# ---------------------------------------------------------------------------


def resolve_codex_token() -> str:
    """Resolve one active Codex credential with bounded refresh/import recovery."""
    valid_token = ""
    valid_revision = ""
    enrich_from_cli = False
    with file_lock(_lock_path()):
        store = _read_auth_store()
        needs_dpapi_migration = bool(
            sys.platform == "win32"
            and store
            and not _auth_store_uses_dpapi()
        )
        raw = store.get("codex")
        raw = raw if isinstance(raw, dict) else {}
        codex = normalise_codex_credential(
            raw,
            source=str(raw.get("source") or "auth_store"),
        )
        if (raw and raw != codex) or needs_dpapi_migration:
            store["codex"] = codex
            try:
                _write_auth_store(store)
            except OSError as exc:
                logger.warning(
                    "[OAuth] Opportunistic auth.json rewrite failed ({}); "
                    "continuing with the in-memory credential.",
                    exc,
                )
        if is_codex_credential_valid(codex):
            valid_token = str(codex["access_token"])
            valid_revision = codex_credential_revision(codex)
            enrich_from_cli = bool(
                codex["credential_type"] == "oauth"
                and not codex["refresh_token"]
            )

    if valid_token:
        if enrich_from_cli:
            enriched = import_codex_cli_tokens(
                expected_token_fingerprint=codex_token_fingerprint(valid_token),
                expected_current_fingerprint=codex_token_fingerprint(valid_token),
                expected_current_revision=valid_revision,
            )
            if enriched and is_codex_credential_valid(enriched):
                return str(enriched["access_token"])
        return valid_token

    refreshed, refresh_attempted = _refresh_stored_codex_credential()
    if refreshed:
        return refreshed

    imported = import_codex_cli_tokens()
    if imported and is_codex_credential_valid(imported):
        return str(imported["access_token"])
    if imported and imported.get("refresh_token") and not refresh_attempted:
        refreshed, _ = _refresh_stored_codex_credential()
        if refreshed:
            return refreshed

    raise ValueError(
        "No valid Codex credentials found. "
        "Run `codex` in your terminal to generate fresh tokens."
    )


def resolve_anthropic_token() -> str:
    """Resolve an active Anthropic access token.

    Resolution order (Hermes spec):
      1. ANTHROPIC_API_KEY env var / settings (plain API key → used directly)
      2. CLAUDE_CODE_OAUTH_TOKEN env var
      3. macOS Keychain → ~/.claude/.credentials.json
      4. ~/.claude.json primaryApiKey (fallback)
      5. Local auth store (output/tokens/auth.json)
    """
    # 1. Direct API key from settings / env
    api_key = settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        return api_key

    # 2. Claude Code OAuth token env var
    oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    if oauth_token:
        return oauth_token

    # 3. Claude Code credentials (Keychain / .credentials.json)
    creds = read_claude_code_credentials()
    if creds:
        access_token = creds.get("accessToken", "")
        expires_at = creds.get("expiresAt", 0)
        if access_token and is_token_valid(expires_at):
            return access_token
        # Try refresh
        refresh_token = creds.get("refreshToken", "")
        if refresh_token:
            try:
                refreshed = refresh_anthropic_oauth_pure(refresh_token)
                # Persist refreshed credentials back to auth store
                with file_lock(_lock_path()):
                    store = _read_auth_store()
                    store["anthropic"] = {
                        "access_token": refreshed["access_token"],
                        "refresh_token": refreshed["refresh_token"],
                        "expires_at_ms": refreshed["expires_at_ms"],
                    }
                    _write_auth_store(store)
                return refreshed["access_token"]
            except Exception as exc:
                logger.warning(
                    "Anthropic token refresh failed: {}",
                    redact_secrets(exc),
                )

    # 4. ~/.claude.json fallback (primaryApiKey)
    claude_json = Path.home() / ".claude.json"
    if claude_json.exists():
        data = json.loads(claude_json.read_text(encoding="utf-8"))
        primary_key = data.get("primaryApiKey", "")
        if primary_key:
            return primary_key

    # 5. Local auth store
    with file_lock(_lock_path()):
        store = _read_auth_store()
        anthro = store.get("anthropic", {})
        if anthro.get("access_token") and is_token_valid(
            anthro.get("expires_at_ms", 0)
        ):
            return anthro["access_token"]

    raise ValueError(
        "No valid Anthropic credentials found. Set ANTHROPIC_API_KEY, "
        "CLAUDE_CODE_OAUTH_TOKEN, or run Claude Code setup-token."
    )
