"""providers/codex_adapter.py — OpenAI Codex Responses API adapter (Hermes spec).

Implements ChatCodex inheriting from LangChain's BaseChatModel to communicate
with OpenAI's chat completions API using OAuth tokens.

This uses the standard OpenAI chat completions endpoint through LangChain's
ChatOpenAI wrapper, authenticated with the Codex OAuth access token.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.failure_taxonomy import classify_exception
from core.settings import settings
from providers.oauth_manager import resolve_codex_token
from utils.logger_config import logger

if TYPE_CHECKING:
    from langchain_openai import ChatOpenAI


def _uses_completion_token_limit(model_name: str) -> bool:
    """Return True for OpenAI model families that reject legacy max_tokens."""
    normalized = model_name.lower()
    return normalized.startswith(("o1", "o3", "o4", "gpt-5"))


def _chat_openai_kwargs(
    *,
    model_name: str,
    access_token: str,
    request_timeout: int,
    max_tokens: int,
    temperature: float,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "model": model_name,
        "api_key": access_token,
        "request_timeout": request_timeout,
    }
    if _uses_completion_token_limit(model_name):
        kwargs["max_completion_tokens"] = max_tokens
    else:
        kwargs["temperature"] = temperature
        kwargs["max_tokens"] = max_tokens
    return kwargs


def get_codex_flash_llm() -> ChatOpenAI:
    """Create a Codex Flash-tier (gpt-4o-mini) instance.

    Uses the resolved OAuth token for authentication via LangChain's ChatOpenAI.
    """
    from langchain_openai import ChatOpenAI

    access_token = resolve_codex_token()

    try:
        model_name = settings.CODEX_FLASH_MODEL
        return ChatOpenAI(
            **_chat_openai_kwargs(
                model_name=model_name,
                access_token=access_token,
                request_timeout=60,
                max_tokens=4000,
                temperature=0.1,
            )
        )
    except Exception as exc:
        failure = classify_exception(exc, source="codex")
        logger.error(f"[Codex] Flash init failed: {failure.model_dump()}")
        raise


def get_codex_pro_llm() -> ChatOpenAI:
    """Create a Codex Pro-tier (gpt-4o) instance.

    Uses the resolved OAuth token for authentication via LangChain's ChatOpenAI.
    """
    from langchain_openai import ChatOpenAI

    access_token = resolve_codex_token()

    try:
        model_name = settings.CODEX_PRO_MODEL
        return ChatOpenAI(
            **_chat_openai_kwargs(
                model_name=model_name,
                access_token=access_token,
                request_timeout=90,
                max_tokens=10000,
                temperature=0.3,
            )
        )
    except Exception as exc:
        failure = classify_exception(exc, source="codex")
        logger.error(f"[Codex] Pro init failed: {failure.model_dump()}")
        raise
