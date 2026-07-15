import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Annotated, Any, Optional

import typer

from app.cli.ui.console import console
from core.settings import settings
from providers.oauth_manager import (
    _lock_path,
    _read_auth_store,
    _write_auth_store,
    file_lock,
    normalise_codex_credential,
)
from utils.secret_redaction import redact_secrets

app = typer.Typer(help="Manage authentication tokens for LLM providers.")

def _codex_device_code_login() -> Optional[dict[str, Any]]:
    """Menjalankan Device Code flow untuk OpenAI Codex."""
    issuer = "https://auth.openai.com"
    client_id = settings.CODEX_OAUTH_CLIENT_ID

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    console.print("[idx.muted]⏳ Requesting device code dari OpenAI...[/idx.muted]")

    req = urllib.request.Request(
        f"{issuer}/api/accounts/deviceauth/usercode",
        data=json.dumps({"client_id": client_id}).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            device_data = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        console.print("[idx.error]Gagal request device code: " + f"{redact_secrets(e)}[/idx.error]")
        return None

    user_code = device_data.get("user_code", "")
    device_auth_id = device_data.get("device_auth_id", "")
    poll_interval = max(3, int(device_data.get("interval", "5")))

    if not user_code or not device_auth_id:
        console.print("[idx.error]❌ Respons tidak valid dari OpenAI.[/idx.error]")
        return None

    console.print("\n[idx.header]To continue, follow these steps:[/idx.header]\n")
    console.print("  1. Open this URL in your browser:")
    console.print(f"     [idx.highlight]{issuer}/codex/device[/idx.highlight]\n")
    console.print("  2. Enter this code:")
    console.print(f"     [idx.ok]{user_code}[/idx.ok]\n")
    console.print(
        "[idx.muted]Waiting for sign-in... (press Ctrl+C to cancel)[/idx.muted]"
    )

    max_wait = 15 * 60
    start_time = time.monotonic()
    code_resp = None

    while time.monotonic() - start_time < max_wait:
        time.sleep(poll_interval)

        poll_req = urllib.request.Request(
            f"{issuer}/api/accounts/deviceauth/token",
            data=json.dumps(
                {"device_auth_id": device_auth_id, "user_code": user_code}
            ).encode("utf-8"),
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(poll_req, timeout=15) as resp:
                if resp.status == 200:
                    code_resp = json.loads(resp.read().decode())
                    break
        except urllib.error.HTTPError as e:
            if e.code in [403, 404]:
                continue
            else:
                console.print(f"[idx.error]❌ Polling error: {e.code}[/idx.error]")
                return None
        except Exception:
            continue

    if not code_resp:
        console.print("\n[idx.error]❌ Login timed out.[/idx.error]")
        return None

    authorization_code = code_resp.get("authorization_code")
    code_verifier = code_resp.get("code_verifier")
    redirect_uri = f"{issuer}/deviceauth/callback"

    if not authorization_code or not code_verifier:
        console.print("[idx.error]❌ Data authorization tidak lengkap.[/idx.error]")
        return None

    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")

    token_req = urllib.request.Request(
        f"{issuer}/oauth/token",
        data=data,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": headers["User-Agent"],
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(token_req, timeout=15) as resp:
            tokens = json.loads(resp.read().decode())
            credential = normalise_codex_credential(
                tokens,
                source="device_code",
                auth_mode="chatgpt",
            )
            return credential if credential["access_token"] else None
    except Exception as e:
        console.print("[idx.error]Gagal menukar token: " + f"{redact_secrets(e)}[/idx.error]")
        return None


def _add_token(
    provider: str,
    token: str | dict[str, Any],
    *,
    source: str = "manual",
) -> None:
    if not token:
        console.print("[idx.error]Error: Token tidak boleh kosong.[/idx.error]")
        return

    if provider == "codex":
        payload = token if isinstance(token, dict) else {"access_token": token}
        token_data = normalise_codex_credential(
            payload,
            source=source,
            auth_mode=str(payload.get("auth_mode") or ""),
        )
        if not token_data["access_token"]:
            console.print(
                "[idx.error]Error: Access token tidak boleh kosong.[/idx.error]"
            )
            return
    else:
        token_data = {
            "access_token": str(token),
            "refresh_token": "",
            "expires_in": 0,
            "expires_at_ms": 0,
            "credential_type": "managed_api_key",
            "source": source,
        }

    try:
        with file_lock(_lock_path()):
            store = _read_auth_store()
            store[provider] = token_data
            _write_auth_store(store)

        console.print(
            f"\n[idx.ok]Token provider '{provider}' berhasil disimpan.[/idx.ok]"
        )
        console.print(
            f"   [idx.muted]Sistem siap menggunakan {provider}.[/idx.muted]"
        )
    except Exception as exc:
        console.print(
            "[idx.error]Gagal menyimpan token: "
            f"{redact_secrets(exc)}[/idx.error]"
        )


@app.command(name="add")
def auth_add_command(
    provider: Annotated[
        str, typer.Argument(help="Nama provider (openai-codex, anthropic, gemini)")
    ],
    managed_key: Annotated[
        bool,
        typer.Option(
            "--managed-key",
            help="Prompt aman untuk managed Codex API key (tidak masuk argv).",
        ),
    ] = False,
) -> None:
    """Tambahkan token tanpa mengekspos secret melalui command-line arguments."""
    provider_raw = provider.lower()
    credential: str | dict[str, Any] | None = None
    source = "manual"

    if provider_raw in ["openai-codex", "codex"]:
        provider_key = "codex"
        if managed_key:
            import getpass

            managed_secret = getpass.getpass(
                "Masukkan managed Codex API key: "
            ).strip()
            if not managed_secret:
                raise typer.Exit(code=1)
            credential = {
                "access_token": managed_secret,
                "credential_type": "managed_api_key",
                "auth_mode": "managed_api_key",
            }
            source = "managed_key_prompt"
        else:
            console.print(
                "[idx.header]Memulai Device Code Login untuk OpenAI Codex...[/idx.header]"
            )
            credential = _codex_device_code_login()
            source = "device_code"
            if not credential:
                raise typer.Exit(code=1)
    elif provider_raw == "anthropic":
        if managed_key:
            raise typer.BadParameter("--managed-key hanya untuk provider codex")
        provider_key = "anthropic"
    elif provider_raw == "gemini":
        if managed_key:
            raise typer.BadParameter("--managed-key hanya untuk provider codex")
        provider_key = "gemini"
    else:
        console.print(
            f"[idx.error]Provider '{provider_raw}' tidak didukung.[/idx.error]"
        )
        raise typer.Exit(code=1)

    if not credential:
        import getpass

        credential = getpass.getpass(
            f"Masukkan access token untuk {provider_key}: "
        ).strip()

    _add_token(provider_key, credential, source=source)


__all__ = ["app"]
