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


def get_codex_flash_llm() -> ChatOpenAI:
    """Create a Codex Flash-tier (gpt-4o-mini) instance.

    Uses the resolved OAuth token for authentication via LangChain's ChatOpenAI.
    """
    from langchain_openai import ChatOpenAI

    access_token = resolve_codex_token()

    try:
        return ChatOpenAI(
            model=settings.CODEX_FLASH_MODEL,
            api_key=access_token,
            temperature=0.1,
            max_tokens=4000,
            request_timeout=60,
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
        return ChatOpenAI(
            model=settings.CODEX_PRO_MODEL,
            api_key=access_token,
            temperature=0.3,
            max_tokens=10000,
            request_timeout=90,
        )
    except Exception as exc:
        failure = classify_exception(exc, source="codex")
        logger.error(f"[Codex] Pro init failed: {failure.model_dump()}")
        raise
