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
    from providers.codex_responses_llm import ChatCodexResponses


def get_codex_flash_llm() -> "ChatCodexResponses":
    """Create a Codex Flash-tier instance.

    Uses the resolved OAuth token for authentication via custom ChatCodexResponses
    to hit the Responses API.
    """
    from providers.codex_responses_llm import ChatCodexResponses
    from pydantic import SecretStr

    access_token = resolve_codex_token()

    try:
        model_name = settings.CODEX_FLASH_MODEL
        kwargs = {
            "model": model_name,
            "api_key": SecretStr(access_token),
            "request_timeout": 60,
            "reasoning_effort": settings.CODEX_FLASH_REASONING_EFFORT,
        }

        if (
            model_name.startswith("o1")
            or model_name.startswith("o3")
            or model_name.startswith("o4")
        ):
            kwargs["max_completion_tokens"] = 4000
        else:
            kwargs["temperature"] = 0.1
            kwargs["max_tokens"] = 4000

        return ChatCodexResponses(**kwargs)
    except Exception as exc:
        failure = classify_exception(exc, source="codex")
        logger.error(f"[Codex] Flash init failed: {failure.model_dump()}")
        raise


def get_codex_pro_llm() -> "ChatCodexResponses":
    """Create a Codex Pro-tier instance.

    Uses the resolved OAuth token for authentication via custom ChatCodexResponses
    to hit the Responses API.
    """
    from providers.codex_responses_llm import ChatCodexResponses
    from pydantic import SecretStr

    access_token = resolve_codex_token()

    try:
        model_name = settings.CODEX_PRO_MODEL
        kwargs = {
            "model": model_name,
            "api_key": SecretStr(access_token),
            "request_timeout": 90,
            "reasoning_effort": settings.CODEX_PRO_REASONING_EFFORT,
        }

        # OpenAI reasoning models (o1, o3, o4) do not support temperature
        # and prefer max_completion_tokens over max_tokens.
        # ChatCodexResponses intentionally does not serialize token-limit fields:
        # the ChatGPT Codex backend rejects max_output_tokens in live probes.
        if (
            model_name.startswith("o1")
            or model_name.startswith("o3")
            or model_name.startswith("o4")
        ):
            kwargs["max_completion_tokens"] = 10000
        else:
            kwargs["temperature"] = 0.3
            kwargs["max_tokens"] = 10000

        return ChatCodexResponses(**kwargs)
    except Exception as exc:
        failure = classify_exception(exc, source="codex")
        logger.error(f"[Codex] Pro init failed: {failure.model_dump()}")
        raise
