from __future__ import annotations

import re
from pathlib import Path

import typer
from rich.panel import Panel

from app.cli.ui.console import console

# Path to the .env file in the root of the project
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"

PROVIDERS = {
    "1": "gemini",
    "2": "anthropic",
    "3": "codex",
}

# Model strings verified via official API docs (June 2026)
# Sources:
#   Gemini  : https://ai.google.dev/gemini-api/docs/models
#   Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
#   OpenAI  : https://platform.openai.com/docs/models
MODELS = {
    "gemini": {
        # gemini-3.5-flash: GA since May 19, 2026
        # gemini-3.1-flash-lite: low-latency tier (3.1 series)
        # gemini-2.5-flash / 2.5-flash-lite: still available;
        #   NOTE: gemini-2.0-flash* was shut down June 1, 2026
        "flash": [
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        ],
        # gemini-3.1-pro-preview: current flagship (GA Feb 2026)
        # gemini-2.5-pro: previous stable pro tier
        "pro": [
            "gemini-3.1-pro-preview",
            "gemini-2.5-pro",
        ],
    },
    "anthropic": {
        # claude-haiku-4-5-20251001: fastest, lowest cost
        # claude-sonnet-4-6: balanced speed + intelligence
        # Note: claude-3-5-haiku-latest and claude-3 series retired Apr 2026
        "flash": [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-4-6",
        ],
        # claude-opus-4-8: most capable (May 2026)
        # claude-opus-4-7: previous flagship (Apr 2026)
        # claude-sonnet-4-6: best price-performance
        "pro": [
            "claude-opus-4-8",
            "claude-opus-4-7",
            "claude-sonnet-4-6",
        ],
    },
    "codex": {
        # gpt-5.4-mini / gpt-5.3-codex: fast & cheap options for ChatGPT Plus / Responses API
        "flash": [
            "gpt-5.4-mini",
            "gpt-5.3-codex",
        ],
        # gpt-5.5 / gpt-5.4 / gpt-5.3-codex-spark: high-capability frontier models
        "pro": [
            "gpt-5.5",
            "gpt-5.4",
            "gpt-5.3-codex-spark",
        ],
    },
}


def update_env_file(key: str, value: str) -> None:
    """Update or add a variable in .env."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    content = ENV_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)

    if pattern.search(content):
        new_content = pattern.sub(f"{key}={value}", content)
    else:
        if not content.endswith("\n"):
            content += "\n"
        new_content = content + f"{key}={value}\n"

    ENV_PATH.write_text(new_content, encoding="utf-8")


def get_current_provider() -> str:
    """Read the active provider from .env, defaulting to gemini."""
    if not ENV_PATH.exists():
        return "gemini"
    content = ENV_PATH.read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_LLM_PROVIDER=(.*)$", content, re.MULTILINE)
    return match.group(1).strip() if match else "gemini"


def _get_current_models(provider: str) -> tuple[str, str]:
    """Return (flash_model, pro_model) for provider from .env."""
    if not ENV_PATH.exists():
        return "(default)", "(default)"
    content = ENV_PATH.read_text(encoding="utf-8")
    p = provider.upper()

    def _read(key: str) -> str:
        m = re.search(rf"^{key}=(.*)$", content, re.MULTILINE)
        return m.group(1).strip() if m else "(default)"

    return _read(f"{p}_FLASH_MODEL"), _read(f"{p}_PRO_MODEL")


def select_model(provider: str, tier: str) -> str:
    choices = MODELS[provider][tier]
    console.print(
        f"\nPilih model untuk [idx.highlight]{provider.capitalize()} - {tier.capitalize()} Tier[/idx.highlight]:"
    )
    for i, m in enumerate(choices, 1):
        console.print(f"  [[idx.highlight]{i}[/idx.highlight]] {m}")
    console.print("  [[idx.highlight]c[/idx.highlight]] Masukkan nama model kustom")

    choice = input(f"Pilihan Anda [1-{len(choices)}/c]: ").strip().lower()

    if choice == "c":
        custom_model = input("Masukkan nama model kustom: ").strip()
        return custom_model if custom_model else choices[0]

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    except ValueError:
        pass

    console.print("[idx.warn]Pilihan tidak valid, menggunakan default.[/idx.warn]")
    return choices[0]


def model_command(
    provider: str = typer.Argument(None, help="Nama provider (gemini, anthropic, codex)"),
) -> None:
    """Pilih Default Model/Provider LLM dan set spesifik model untuk Flash/Pro."""
    if provider:
        provider = provider.lower()
        if provider not in ["gemini", "anthropic", "codex"]:
            console.print(
                f"[idx.error]❌ Provider '{provider}' tidak valid.[/idx.error]"
            )
            raise typer.Exit(1)

        update_env_file("DEFAULT_LLM_PROVIDER", provider)
        console.print(
            f"[idx.ok]✅ Default provider telah diubah ke: [idx.highlight]{provider}[/idx.highlight][/idx.ok]"
        )
        return

    current = get_current_provider()
    flash_model, pro_model = _get_current_models(current)

    console.print(
        Panel(
            f"[idx.label]Provider:[/idx.label]     [idx.highlight]{current.capitalize()}[/idx.highlight]\n"
            f"[idx.label]Flash:[/idx.label]        [idx.value]{flash_model}[/idx.value]\n"
            f"[idx.label]Pro:[/idx.label]          [idx.value]{pro_model}[/idx.value]",
            title="[idx.header]Current LLM Configuration[/idx.header]",
            border_style="idx.header",
            expand=False,
        )
    )

    console.print("\nPilih provider yang ingin digunakan sebagai default:")
    for num, name in PROVIDERS.items():
        marker = " [idx.ok]← (Aktif)[/idx.ok]" if name == current else ""
        console.print(f"  [[idx.highlight]{num}[/idx.highlight]] {name.capitalize()}{marker}")
    console.print("  [[idx.highlight]0[/idx.highlight]] Batal / Keluar")

    try:
        choice = input("\nMasukkan pilihan Anda [0-3]: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[idx.warn]Dibatalkan.[/idx.warn]")
        raise typer.Exit()

    if choice == "0" or not choice:
        console.print("[idx.warn]Dibatalkan.[/idx.warn]")
        raise typer.Exit()

    if choice in PROVIDERS:
        selected_provider = PROVIDERS[choice]

        flash_model = select_model(selected_provider, "flash")
        pro_model = select_model(selected_provider, "pro")

        update_env_file("DEFAULT_LLM_PROVIDER", selected_provider)

        if selected_provider == "gemini":
            update_env_file("GEMINI_FLASH_MODEL", flash_model)
            update_env_file("GEMINI_PRO_MODEL", pro_model)
        elif selected_provider == "anthropic":
            update_env_file("ANTHROPIC_FLASH_MODEL", flash_model)
            update_env_file("ANTHROPIC_PRO_MODEL", pro_model)
        elif selected_provider == "codex":
            update_env_file("CODEX_FLASH_MODEL", flash_model)
            update_env_file("CODEX_PRO_MODEL", pro_model)

        console.print(
            Panel(
                f"[idx.label]Provider:[/idx.label]     [idx.highlight]{selected_provider.capitalize()}[/idx.highlight]\n"
                f"[idx.label]Flash:[/idx.label]        [idx.value]{flash_model}[/idx.value]\n"
                f"[idx.label]Pro:[/idx.label]          [idx.value]{pro_model}[/idx.value]",
                title="[idx.ok]✅ Configuration Updated[/idx.ok]",
                border_style="idx.ok",
                expand=False,
            )
        )
    else:
        console.print(
            f"\n[idx.error]❌ Pilihan '{choice}' tidak valid.[/idx.error]"
        )
        raise typer.Exit(1)
