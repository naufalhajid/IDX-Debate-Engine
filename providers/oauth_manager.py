"""providers/oauth_manager.py — Token storage & credential resolution (Hermes spec).

Replicates the exact authentication flows from hermes-agent's auth module:
1. File-based mutex lock for concurrent token refresh safety.
2. OpenAI Codex Device Code Flow (client_id = app_EMoamEEZ73f0CkXaXp7hrann).
3. Codex CLI auto-import (~/.codex/auth.json).
4. Anthropic OAuth token resolution (env var → keychain → credentials file → claude.json).
5. Token persistence to output/tokens/auth.json with owner-only permissions (0o600).
"""

from __future__ import annotations

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
    return json.loads(path.read_text(encoding="utf-8"))


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
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Codex CLI auto-import (~/.codex/auth.json)
# ---------------------------------------------------------------------------


def import_codex_cli_tokens() -> dict[str, Any] | None:
    """Scan ~/.codex/auth.json for existing Codex CLI tokens.

    Returns token dict or None if not found / invalid.
    """
    codex_auth = Path.home() / ".codex" / "auth.json"
    if not codex_auth.exists():
        return None

    data = json.loads(codex_auth.read_text(encoding="utf-8"))
    access_token = data.get("access_token") or data.get("token", "")
    if not access_token:
        return None

    expires_at = data.get("expires_at", 0)
    if isinstance(expires_at, (int, float)) and expires_at > 0:
        # Convert to milliseconds if in seconds
        if expires_at < 1e12:
            expires_at = int(expires_at * 1000)
    else:
        expires_at = 0

    token_data = {
        "access_token": access_token,
        "refresh_token": data.get("refresh_token", ""),
        "expires_at_ms": int(expires_at),
    }

    # Persist to our own store
    with file_lock(_lock_path()):
        store = _read_auth_store()
        store["codex"] = token_data
        _write_auth_store(store)

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
            logger.debug(f"Anthropic token refresh failed at {endpoint}: {exc}")
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
    """Resolve an active Codex access token.

    Resolution order:
      1. Local auth store (output/tokens/auth.json)
      2. Codex CLI auto-import (~/.codex/auth.json)
      3. Raise if none found
    """
    with file_lock(_lock_path()):
        store = _read_auth_store()
        codex = store.get("codex", {})
        if codex.get("access_token"):
            if is_token_valid(codex.get("expires_at_ms", 0)):
                return codex["access_token"]
            # Token expired — try refresh
            refresh_token = codex.get("refresh_token", "")
            if refresh_token:
                try:
                    data = urllib.parse.urlencode(
                        {
                            "grant_type": "refresh_token",
                            "refresh_token": refresh_token,
                            "client_id": settings.CODEX_OAUTH_CLIENT_ID,
                        }
                    ).encode()
                    req = urllib.request.Request(
                        "https://auth.openai.com/oauth/token",
                        data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        method="POST",
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        result = json.loads(resp.read().decode())
                    new_token = result.get("access_token", "")
                    if new_token:
                        codex["access_token"] = new_token
                        codex["refresh_token"] = result.get(
                            "refresh_token", refresh_token
                        )
                        codex["expires_at_ms"] = (
                            int(time.time() * 1000)
                            + result.get("expires_in", 3600) * 1000
                        )
                        store["codex"] = codex
                        _write_auth_store(store)
                        logger.debug("Codex token refreshed successfully.")
                        return new_token
                except Exception as exc:
                    logger.warning(f"Codex token refresh failed: {exc}")

    # Fallback: try importing from Codex CLI
    imported = import_codex_cli_tokens()
    if imported and imported.get("access_token"):
        return imported["access_token"]

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
                logger.warning(f"Anthropic token refresh failed: {exc}")

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
