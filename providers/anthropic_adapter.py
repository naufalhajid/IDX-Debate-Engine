"""providers/anthropic_adapter.py — Anthropic client construction (Hermes spec).

Replicates the client construction logic from hermes-agent's anthropic_adapter:
- Detect if a key is an OAuth token (sk-ant-* but not sk-ant-api, eyJ JWT, cc-)
- Set it to SDK's auth_token (not api_key) to trigger Bearer authorization
- Inject Claude Code identity headers for OAuth routing
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from core.failure_taxonomy import classify_exception
from core.settings import settings
from providers.oauth_manager import detect_claude_code_version, resolve_anthropic_token
from utils.logger_config import logger

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic

# Beta headers — matches Hermes spec for enhanced features on Claude models.
_COMMON_BETAS = [
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
]

# Additional beta headers required for OAuth/subscription auth.
_OAUTH_ONLY_BETAS = [
    "claude-code-20250219",
    "oauth-2025-04-20",
]


def _is_oauth_token(key: str) -> bool:
    """Check if the key is an Anthropic OAuth/setup token.

    Positively identifies Anthropic OAuth tokens by their key format:
    - ``sk-ant-`` prefix (but NOT ``sk-ant-api``) → setup tokens, managed keys
    - ``eyJ`` prefix → JWTs from the Anthropic OAuth flow
    - ``cc-`` prefix → Claude Code OAuth access tokens
    """
    if not key:
        return False
    if key.startswith("sk-ant-api"):
        return False
    if key.startswith("sk-ant-"):
        return True
    if key.startswith("eyJ"):
        return True
    if key.startswith("cc-"):
        return True
    return False


def _build_client_kwargs(api_key: str) -> dict[str, Any]:
    """Build the kwargs dict for ChatAnthropic based on key type.

    Replicates hermes-agent's build_anthropic_client logic for OAuth detection
    and header injection.
    """
    kwargs: dict[str, Any] = {
        "default_headers": {},
    }

    if _is_oauth_token(api_key):
        # OAuth access token / setup-token → Bearer auth + Claude Code identity.
        all_betas = _COMMON_BETAS + _OAUTH_ONLY_BETAS
        # LangChain ChatAnthropic wraps the Anthropic SDK. Setting the key
        # as anthropic_api_key with a recognised OAuth prefix triggers Bearer
        # auth internally when the SDK detects the token format.
        kwargs["anthropic_api_key"] = api_key
        kwargs["default_headers"] = {
            "anthropic-beta": ",".join(all_betas),
            "user-agent": f"claude-cli/{detect_claude_code_version()} (external, cli)",
            "x-app": "cli",
        }
    else:
        # Regular API key → x-api-key header + common betas
        kwargs["anthropic_api_key"] = api_key
        if _COMMON_BETAS:
            kwargs["default_headers"] = {
                "anthropic-beta": ",".join(_COMMON_BETAS),
            }

    return kwargs


def get_anthropic_flash_llm() -> ChatAnthropic:
    """Create an Anthropic Flash-tier (Haiku) instance.

    Uses the same token-resolution and header-injection logic as hermes-agent.
    """
    from langchain_anthropic import ChatAnthropic

    api_key = resolve_anthropic_token()
    client_kwargs = _build_client_kwargs(api_key)

    try:
        return ChatAnthropic(
            model=settings.ANTHROPIC_FLASH_MODEL,
            temperature=0.1,
            max_tokens=4000,
            timeout=60,
            **client_kwargs,
        )
    except Exception as exc:
        failure = classify_exception(exc, source="anthropic")
        logger.error(f"[Anthropic] Flash init failed: {failure.model_dump()}")
        raise


def get_anthropic_pro_llm() -> ChatAnthropic:
    """Create an Anthropic Pro-tier (Sonnet) instance.

    Uses the same token-resolution and header-injection logic as hermes-agent.
    """
    from langchain_anthropic import ChatAnthropic

    api_key = resolve_anthropic_token()
    client_kwargs = _build_client_kwargs(api_key)

    try:
        return ChatAnthropic(
            model=settings.ANTHROPIC_PRO_MODEL,
            temperature=0.3,
            max_tokens=10000,
            timeout=90,
            **client_kwargs,
        )
    except Exception as exc:
        failure = classify_exception(exc, source="anthropic")
        logger.error(f"[Anthropic] Pro init failed: {failure.model_dump()}")
        raise
