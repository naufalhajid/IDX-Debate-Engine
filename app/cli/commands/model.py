from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

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

CODEX_REASONING_CHOICES = ("low", "medium", "high", "xhigh")
CODEX_REASONING_DEFAULTS = {
    "flash": "medium",
    "pro": "xhigh",
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


def _get_current_codex_reasoning() -> tuple[str, str]:
    """Return (flash_reasoning, pro_reasoning) for Codex from .env."""
    if not ENV_PATH.exists():
        return "(default)", "(default)"
    content = ENV_PATH.read_text(encoding="utf-8")

    def _read(key: str) -> str:
        m = re.search(rf"^{key}=(.*)$", content, re.MULTILINE)
        return m.group(1).strip() if m else "(default)"

    return _read("CODEX_FLASH_REASONING_EFFORT"), _read(
        "CODEX_PRO_REASONING_EFFORT"
    )


def _configured_or_default(value: str, provider: str, tier: str) -> str:
    if value and value != "(default)":
        return value
    return MODELS[provider][tier][0]


def _normalize_reasoning_effort(value: str, *, tier: str) -> str:
    effort = value.strip().lower()
    if effort not in CODEX_REASONING_CHOICES:
        console.print(
            "[idx.error]Invalid Codex reasoning effort "
            f"'{value}'. Choose: low, medium, high, xhigh. "
            "Use 'xhigh' for Extra High.[/idx.error]"
        )
        raise typer.Exit(1)
    return effort


def select_model(provider: str, tier: str) -> str:
    choices = MODELS[provider][tier]
    console.print(
        f"\nSelect model for [idx.highlight]{provider.capitalize()} - {tier.capitalize()} Tier[/idx.highlight]:"
    )
    for i, m in enumerate(choices, 1):
        console.print(f"  [[idx.highlight]{i}[/idx.highlight]] {m}")
    console.print("  [[idx.highlight]c[/idx.highlight]] Enter custom model name")

    choice = input(f"Your choice [1-{len(choices)}/c]: ").strip().lower()

    if choice == "c":
        custom_model = input("Enter custom model name: ").strip()
        return custom_model if custom_model else choices[0]

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    except ValueError:
        pass

    console.print("[idx.warn]Invalid choice, using default.[/idx.warn]")
    return choices[0]


def select_codex_reasoning(tier: str) -> str:
    default = CODEX_REASONING_DEFAULTS[tier]
    console.print(
        f"\nSelect reasoning for [idx.highlight]Codex - {tier.capitalize()} Tier[/idx.highlight]:"
    )
    for i, effort in enumerate(CODEX_REASONING_CHOICES, 1):
        marker = " [idx.ok]<- Recommended[/idx.ok]" if effort == default else ""
        console.print(f"  [[idx.highlight]{i}[/idx.highlight]] {effort}{marker}")

    choice = input(
        f"Your choice [1-{len(CODEX_REASONING_CHOICES)}] "
        f"(default: {default}): "
    ).strip().lower()

    if not choice:
        return default
    if choice in CODEX_REASONING_CHOICES:
        return choice
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(CODEX_REASONING_CHOICES):
            return CODEX_REASONING_CHOICES[idx]
    except ValueError:
        pass

    console.print("[idx.warn]Invalid choice, using recommended default.[/idx.warn]")
    return default


def _configure_codex_non_interactive(
    flash_reasoning: Optional[str],
    pro_reasoning: Optional[str],
) -> tuple[str, str, str, str]:
    current_flash_model, current_pro_model = _get_current_models("codex")
    current_flash_reasoning, current_pro_reasoning = _get_current_codex_reasoning()

    flash_model = _configured_or_default(current_flash_model, "codex", "flash")
    pro_model = _configured_or_default(current_pro_model, "codex", "pro")
    flash_effort_source = (
        flash_reasoning
        or (
            current_flash_reasoning
            if current_flash_reasoning != "(default)"
            else CODEX_REASONING_DEFAULTS["flash"]
        )
    )
    pro_effort_source = (
        pro_reasoning
        or (
            current_pro_reasoning
            if current_pro_reasoning != "(default)"
            else CODEX_REASONING_DEFAULTS["pro"]
        )
    )
    flash_effort = _normalize_reasoning_effort(flash_effort_source, tier="flash")
    pro_effort = _normalize_reasoning_effort(pro_effort_source, tier="pro")

    update_env_file("DEFAULT_LLM_PROVIDER", "codex")
    update_env_file("CODEX_FLASH_MODEL", flash_model)
    update_env_file("CODEX_PRO_MODEL", pro_model)
    update_env_file("CODEX_FLASH_REASONING_EFFORT", flash_effort)
    update_env_file("CODEX_PRO_REASONING_EFFORT", pro_effort)

    return flash_model, pro_model, flash_effort, pro_effort


def model_command(
    provider: Optional[str] = typer.Argument(
        None, help="Provider name: gemini, anthropic, or codex."
    ),
    flash_reasoning: Optional[str] = typer.Option(
        None,
        "--flash-reasoning",
        help="Codex flash reasoning effort: low, medium, high, or xhigh.",
    ),
    pro_reasoning: Optional[str] = typer.Option(
        None,
        "--pro-reasoning",
        help="Codex pro reasoning effort: low, medium, high, or xhigh.",
    ),
) -> None:
    """Switch LLM provider and configure flash/pro model variants."""
    reasoning_flags_used = flash_reasoning is not None or pro_reasoning is not None

    if provider:
        provider = provider.lower()
        if provider not in ["gemini", "anthropic", "codex"]:
            console.print(
                f"[idx.error]Provider '{provider}' is not valid. Choose: gemini, anthropic, codex.[/idx.error]"
            )
            raise typer.Exit(1)

        if reasoning_flags_used and provider != "codex":
            console.print(
                "[idx.error]Reasoning flags only apply to provider 'codex'.[/idx.error]"
            )
            raise typer.Exit(1)

        if provider == "codex" and reasoning_flags_used:
            flash_model, pro_model, flash_effort, pro_effort = (
                _configure_codex_non_interactive(flash_reasoning, pro_reasoning)
            )
            console.print(
                Panel(
                    f"[idx.label]Provider:[/idx.label]           [idx.highlight]Codex[/idx.highlight]\n"
                    f"[idx.label]Flash:[/idx.label]              [idx.value]{flash_model}[/idx.value]\n"
                    f"[idx.label]Flash Reasoning:[/idx.label]    [idx.value]{flash_effort}[/idx.value]\n"
                    f"[idx.label]Pro:[/idx.label]                [idx.value]{pro_model}[/idx.value]\n"
                    f"[idx.label]Pro Reasoning:[/idx.label]      [idx.value]{pro_effort}[/idx.value]",
                    title="[idx.ok]Configuration Updated[/idx.ok]",
                    border_style="idx.ok",
                    expand=False,
                )
            )
            return

        update_env_file("DEFAULT_LLM_PROVIDER", provider)
        console.print(
            f"[idx.ok]Default provider changed to: [idx.highlight]{provider}[/idx.highlight][/idx.ok]"
        )
        return

    if reasoning_flags_used:
        console.print(
            "[idx.error]Reasoning flags require provider 'codex'. "
            "Example: idx model codex --flash-reasoning medium --pro-reasoning xhigh[/idx.error]"
        )
        raise typer.Exit(1)

    current = get_current_provider()
    flash_model, pro_model = _get_current_models(current)
    panel_lines = [
        f"[idx.label]Provider:[/idx.label]     [idx.highlight]{current.capitalize()}[/idx.highlight]",
        f"[idx.label]Flash:[/idx.label]        [idx.value]{flash_model}[/idx.value]",
        f"[idx.label]Pro:[/idx.label]          [idx.value]{pro_model}[/idx.value]",
    ]
    if current == "codex":
        flash_effort, pro_effort = _get_current_codex_reasoning()
        panel_lines.extend(
            [
                f"[idx.label]Flash Reasoning:[/idx.label] [idx.value]{flash_effort}[/idx.value]",
                f"[idx.label]Pro Reasoning:[/idx.label]   [idx.value]{pro_effort}[/idx.value]",
            ]
        )

    console.print(
        Panel(
            "\n".join(panel_lines),
            title="[idx.header]Current LLM Configuration[/idx.header]",
            border_style="idx.header",
            expand=False,
        )
    )

    console.print("\nSelect a provider to use as default:")
    for num, name in PROVIDERS.items():
        marker = " [idx.ok]<- (Active)[/idx.ok]" if name == current else ""
        console.print(
            f"  [[idx.highlight]{num}[/idx.highlight]] {name.capitalize()}{marker}"
        )
    console.print("  [[idx.highlight]0[/idx.highlight]] Cancel / Exit")

    try:
        choice = input("\nEnter your choice [0-3]: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[idx.warn]Cancelled.[/idx.warn]")
        raise typer.Exit()

    if choice == "0" or not choice:
        console.print("[idx.warn]Cancelled.[/idx.warn]")
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
            flash_reasoning = select_codex_reasoning("flash")
            pro_reasoning = select_codex_reasoning("pro")
            update_env_file("CODEX_FLASH_REASONING_EFFORT", flash_reasoning)
            update_env_file("CODEX_PRO_REASONING_EFFORT", pro_reasoning)

        result_lines = [
            f"[idx.label]Provider:[/idx.label]     [idx.highlight]{selected_provider.capitalize()}[/idx.highlight]",
            f"[idx.label]Flash:[/idx.label]        [idx.value]{flash_model}[/idx.value]",
            f"[idx.label]Pro:[/idx.label]          [idx.value]{pro_model}[/idx.value]",
        ]
        if selected_provider == "codex":
            result_lines.extend(
                [
                    f"[idx.label]Flash Reasoning:[/idx.label] [idx.value]{flash_reasoning}[/idx.value]",
                    f"[idx.label]Pro Reasoning:[/idx.label]   [idx.value]{pro_reasoning}[/idx.value]",
                ]
            )

        console.print(
            Panel(
                "\n".join(result_lines),
                title="[idx.ok]Configuration Updated[/idx.ok]",
                border_style="idx.ok",
                expand=False,
            )
        )
    else:
        console.print(f"\n[idx.error]Choice '{choice}' is not valid.[/idx.error]")
        raise typer.Exit(1)
