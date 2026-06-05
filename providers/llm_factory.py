"""providers/llm_factory.py — Unified LLM factory (Hermes spec).

Single entrypoint for constructing LLM instances across all providers.
Supports runtime provider switching via ContextVar-based isolation.

Usage:
    from providers.llm_factory import get_llm, llm_provider_override

    # Use default provider from settings
    flash = get_llm("flash")
    pro = get_llm("pro")

    # Temporarily use a specific provider
    with llm_provider_override("anthropic"):
        flash = get_llm("flash")  # returns ChatAnthropic
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Literal, Optional

from langchain_core.language_models import BaseChatModel

from core.settings import settings
from utils.logger_config import logger

# ContextVar for scoped provider overrides (Hermes spec)
_active_provider_override: ContextVar[str | None] = ContextVar(
    "active_provider_override",
    default=None,
)


@contextmanager
def llm_provider_override(provider: str) -> Iterator[None]:
    """Temporarily direct all get_llm() calls to a specific provider.

    Provides async-safe, task-scoped provider isolation via ContextVar.
    """
    token = _active_provider_override.set(provider)
    try:
        yield
    finally:
        _active_provider_override.reset(token)


def _resolve_provider(provider: str | None = None) -> str:
    """Resolve the active provider.

    Priority: explicit argument > ContextVar override > settings default.
    """
    if provider:
        return provider.lower()
    override = _active_provider_override.get()
    if override:
        return override.lower()
    return settings.DEFAULT_LLM_PROVIDER.lower()


def get_llm(
    tier: Literal["flash", "pro"],
    provider: Optional[str] = None,
) -> BaseChatModel:
    """Construct an LLM instance for the given tier and provider.

    Args:
        tier: "flash" for fast/cheap tasks, "pro" for reasoning/judgement.
        provider: Optional provider override. If None, resolves from
                  ContextVar or settings.DEFAULT_LLM_PROVIDER.

    Returns:
        A LangChain BaseChatModel instance (ChatGoogleGenerativeAI,
        ChatAnthropic, or ChatOpenAI).

    Raises:
        ValueError: If the provider name is not recognized.
    """
    resolved = _resolve_provider(provider)
    logger.debug(f"[LLM Factory] Constructing {tier} model from provider: {resolved}")

    if resolved == "gemini":
        from providers.gemini import get_flash_llm, get_pro_llm

        return get_flash_llm() if tier == "flash" else get_pro_llm()

    if resolved == "anthropic":
        from providers.anthropic_adapter import (
            get_anthropic_flash_llm,
            get_anthropic_pro_llm,
        )

        return get_anthropic_flash_llm() if tier == "flash" else get_anthropic_pro_llm()

    if resolved == "codex":
        from providers.codex_adapter import get_codex_flash_llm, get_codex_pro_llm

        return get_codex_flash_llm() if tier == "flash" else get_codex_pro_llm()

    raise ValueError(
        f"Unknown LLM provider: {resolved!r}. "
        f"Supported providers: gemini, anthropic, codex"
    )
