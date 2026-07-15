"""tests/test_model_integration.py — Unit tests for multi-model LLM integration.

Tests cover:
  - Token validity checks
  - File lock mechanism
  - Provider resolution logic
  - ContextVar-based provider override isolation
  - OAuth token format detection
  - Failure taxonomy for provider-specific errors
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr


# ---------------------------------------------------------------------------
# Token validity
# ---------------------------------------------------------------------------


class TestTokenValidity:
    """Test is_token_valid() from oauth_manager."""

    def test_no_expiry_is_valid(self):
        from providers.oauth_manager import is_token_valid

        assert is_token_valid(0) is True

    def test_future_expiry_is_valid(self):
        from providers.oauth_manager import is_token_valid

        future_ms = int(time.time() * 1000) + 3600_000  # +1 hour
        assert is_token_valid(future_ms) is True

    def test_past_expiry_is_invalid(self):
        from providers.oauth_manager import is_token_valid

        past_ms = int(time.time() * 1000) - 60_000  # -1 minute
        assert is_token_valid(past_ms) is False

    def test_within_buffer_is_invalid(self):
        from providers.oauth_manager import is_token_valid

        # 30 seconds from now — inside the 60s buffer
        near_ms = int(time.time() * 1000) + 30_000
        assert is_token_valid(near_ms) is False


# ---------------------------------------------------------------------------
# File lock
# ---------------------------------------------------------------------------


class TestFileLock:
    """Test file_lock() context manager."""

    def test_lock_acquires_and_releases(self, tmp_path: Path):
        from providers.oauth_manager import file_lock

        lock_file = str(tmp_path / "test.lock")
        with file_lock(lock_file):
            assert os.path.exists(lock_file)

    def test_concurrent_lock_timeout(self, tmp_path: Path):
        from providers.oauth_manager import file_lock

        lock_file = str(tmp_path / "test.lock")

        # Hold the lock in a nested context — second acquire should timeout
        with file_lock(lock_file):
            with pytest.raises(TimeoutError):
                with file_lock(lock_file, timeout=0.3):
                    pass  # pragma: no cover


# ---------------------------------------------------------------------------
# Auth store persistence
# ---------------------------------------------------------------------------


class TestAuthStore:
    """Test auth.json read/write."""

    def test_write_and_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.oauth_manager.settings",
            MagicMock(TOKEN_STORAGE_DIR=str(tmp_path)),
        )
        from providers.oauth_manager import _read_auth_store, _write_auth_store

        _write_auth_store({"codex": {"access_token": "test123"}})
        store = _read_auth_store()
        assert store["codex"]["access_token"] == "test123"

    def test_read_empty(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.oauth_manager.settings",
            MagicMock(TOKEN_STORAGE_DIR=str(tmp_path)),
        )
        from providers.oauth_manager import _read_auth_store

        assert _read_auth_store() == {}


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


class TestProviderResolution:
    """Test _resolve_provider and ContextVar override."""

    def test_default_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import _resolve_provider

        assert _resolve_provider() == "gemini"

    def test_explicit_provider(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import _resolve_provider

        assert _resolve_provider("anthropic") == "anthropic"

    def test_contextvar_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import _resolve_provider, llm_provider_override

        with llm_provider_override("codex"):
            assert _resolve_provider() == "codex"
        # Outside context, back to default
        assert _resolve_provider() == "gemini"

    def test_explicit_beats_contextvar(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import _resolve_provider, llm_provider_override

        with llm_provider_override("codex"):
            assert _resolve_provider("anthropic") == "anthropic"


# ---------------------------------------------------------------------------
# get_llm factory
# ---------------------------------------------------------------------------


class TestGetLlm:
    """Test get_llm() factory dispatch."""

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import get_llm

        with pytest.raises(ValueError, match="Unknown LLM provider"):
            get_llm("flash", provider="nonexistent")

    @patch("providers.gemini.get_flash_llm")
    def test_gemini_flash(self, mock_flash, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import get_llm

        mock_flash.return_value = MagicMock()
        result = get_llm("flash", provider="gemini")
        mock_flash.assert_called_once()
        assert result is mock_flash.return_value

    @patch("providers.gemini.get_pro_llm")
    def test_gemini_pro(self, mock_pro, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import get_llm

        mock_pro.return_value = MagicMock()
        result = get_llm("pro", provider="gemini")
        mock_pro.assert_called_once()
        assert result is mock_pro.return_value


# ---------------------------------------------------------------------------
# Codex Responses API payload
# ---------------------------------------------------------------------------


class TestCodexResponsesPayload:
    """Test Codex Responses API payload construction."""

    def test_build_api_kwargs_includes_reasoning(self):
        from providers.codex_responses_llm import ChatCodexResponses

        llm = ChatCodexResponses(
            model="gpt-5.5",
            api_key=SecretStr("test-token"),
            reasoning_effort="xhigh",
            max_tokens=1234,
        )

        payload = llm._build_api_kwargs(
            [
                SystemMessage(content="Use exact JSON."),
                HumanMessage(content="Analyze BBCA."),
            ]
        )

        assert payload["reasoning"] == {"effort": "xhigh"}
        assert "max_output_tokens" not in payload
        assert "max_tokens" not in payload
        assert payload["instructions"] == "Use exact JSON."

    def test_build_api_kwargs_omits_empty_reasoning(self):
        from providers.codex_responses_llm import ChatCodexResponses

        llm = ChatCodexResponses(
            model="gpt-5.4-mini",
            api_key=SecretStr("test-token"),
            reasoning_effort="",
            max_tokens=4000,
        )

        payload = llm._build_api_kwargs([HumanMessage(content="ping")])

        assert "reasoning" not in payload
        assert "max_output_tokens" not in payload
        assert "max_tokens" not in payload

    def test_invalid_reasoning_mentions_xhigh_hint(self):
        from providers.codex_responses_llm import ChatCodexResponses

        with pytest.raises(ValueError, match="xhigh"):
            ChatCodexResponses(
                model="gpt-5.5",
                api_key=SecretStr("test-token"),
                reasoning_effort="extra-high",
            )


class TestCodexResponsesAuthLifecycle:
    def test_oauth_uses_chatgpt_codex_transport(self):
        from providers.codex_responses_llm import ChatCodexResponses

        llm = ChatCodexResponses(
            model="gpt-5.5",
            api_key=SecretStr("oauth-token"),
            credential_type="oauth",
        )

        assert str(llm._sync_client.base_url).rstrip("/") == (
            "https://chatgpt.com/backend-api/codex"
        )

    def test_managed_api_key_uses_openai_api_transport(self):
        from providers.codex_responses_llm import ChatCodexResponses

        llm = ChatCodexResponses(
            model="gpt-5.5",
            api_key=SecretStr("sk-managed-test-token"),
            credential_type="managed_api_key",
        )

        assert str(llm._sync_client.base_url).rstrip("/") == (
            "https://api.openai.com/v1"
        )

    def test_sync_request_replays_only_once_after_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from providers.codex_responses_llm import ChatCodexResponses

        class ExpiredTokenError(RuntimeError):
            status_code = 401

        class Responses:
            calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ExpiredTokenError("token_expired")
                return iter(
                    [
                        SimpleNamespace(
                            type="response.completed",
                            response=SimpleNamespace(error=None),
                        )
                    ]
                )

        responses = Responses()
        llm = ChatCodexResponses(
            model="gpt-5.5",
            api_key=SecretStr("oauth-token"),
        )
        llm._sync_client = SimpleNamespace(responses=responses)
        monkeypatch.setattr(
            ChatCodexResponses,
            "_recover_auth_sync",
            lambda self, exc: None,
            raising=False,
        )

        assert list(llm._stream([HumanMessage(content="ping")])) == []
        assert responses.calls == 2

    def test_sync_request_stops_after_second_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from providers.codex_responses_llm import ChatCodexResponses

        class ExpiredTokenError(RuntimeError):
            status_code = 401

        class Responses:
            calls = 0

            def create(self, **_kwargs):
                self.calls += 1
                raise ExpiredTokenError("token_expired")

        responses = Responses()
        llm = ChatCodexResponses(
            model="gpt-5.5",
            api_key=SecretStr("oauth-token"),
        )
        llm._sync_client = SimpleNamespace(responses=responses)
        monkeypatch.setattr(
            ChatCodexResponses,
            "_recover_auth_sync",
            lambda self, exc: None,
            raising=False,
        )

        with pytest.raises(RuntimeError, match="after one credential recovery"):
            list(llm._stream([HumanMessage(content="ping")]))
        assert responses.calls == 2

    @pytest.mark.asyncio
    async def test_async_request_replays_only_once_after_401(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from providers.codex_responses_llm import ChatCodexResponses

        class ExpiredTokenError(RuntimeError):
            status_code = 401

        class Responses:
            calls = 0

            async def create(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise ExpiredTokenError("token_expired")

                async def events():
                    yield SimpleNamespace(
                        type="response.completed",
                        response=SimpleNamespace(error=None),
                    )

                return events()

        async def fake_recovery(self, exc):
            return None

        responses = Responses()
        llm = ChatCodexResponses(
            model="gpt-5.5",
            api_key=SecretStr("oauth-token"),
        )
        llm._client = SimpleNamespace(responses=responses)
        monkeypatch.setattr(
            ChatCodexResponses,
            "_recover_auth_async",
            fake_recovery,
            raising=False,
        )

        chunks = [
            chunk
            async for chunk in llm._astream([HumanMessage(content="ping")])
        ]
        assert chunks == []
        assert responses.calls == 2


# ---------------------------------------------------------------------------
# Codex adapter settings
# ---------------------------------------------------------------------------


class TestCodexAdapter:
    """Test Codex adapter forwards model and reasoning settings."""

    def test_codex_flash_uses_medium_reasoning(self, monkeypatch: pytest.MonkeyPatch):
        calls = []

        class FakeChatCodexResponses:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("providers.codex_adapter.resolve_codex_token", lambda: "t")
        monkeypatch.setattr(
            "providers.codex_adapter.settings",
            MagicMock(
                CODEX_FLASH_MODEL="gpt-5.4-mini",
                CODEX_FLASH_REASONING_EFFORT="medium",
                CODEX_FLASH_REQUEST_TIMEOUT_SECONDS=120,
            ),
        )
        monkeypatch.setattr(
            "providers.codex_responses_llm.ChatCodexResponses",
            FakeChatCodexResponses,
        )

        from providers.codex_adapter import get_codex_flash_llm

        get_codex_flash_llm()

        assert calls[0]["model"] == "gpt-5.4-mini"
        assert calls[0]["reasoning_effort"] == "medium"
        assert calls[0]["request_timeout"] == 120
        assert calls[0]["max_tokens"] == 4000
        assert calls[0]["temperature"] == 0.0

    def test_codex_adapter_error_log_redacts_secret(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from providers import codex_adapter

        secret = "sk-adapter-secret-sentinel"
        captured: list[str] = []

        class FailingChatCodexResponses:
            def __init__(self, **_kwargs):
                raise RuntimeError(f"api_key={secret}")

        monkeypatch.setattr(codex_adapter, "resolve_codex_token", lambda: secret)
        monkeypatch.setattr(
            codex_adapter,
            "get_codex_credential_type",
            lambda _token: "managed_api_key",
        )
        monkeypatch.setattr(
            "providers.codex_responses_llm.ChatCodexResponses",
            FailingChatCodexResponses,
        )
        monkeypatch.setattr(
            codex_adapter.logger,
            "error",
            lambda message, *args: captured.append(str(message).format(*args)),
        )

        with pytest.raises(RuntimeError):
            codex_adapter.get_codex_flash_llm()

        rendered = "\n".join(captured)
        assert secret not in rendered
        assert "[REDACTED]" in rendered

    def test_codex_pro_uses_xhigh_reasoning(self, monkeypatch: pytest.MonkeyPatch):
        calls = []

        class FakeChatCodexResponses:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("providers.codex_adapter.resolve_codex_token", lambda: "t")
        monkeypatch.setattr(
            "providers.codex_adapter.settings",
            MagicMock(
                CODEX_PRO_MODEL="gpt-5.5",
                CODEX_PRO_REASONING_EFFORT="xhigh",
                CODEX_PRO_REQUEST_TIMEOUT_SECONDS=180,
            ),
        )
        monkeypatch.setattr(
            "providers.codex_responses_llm.ChatCodexResponses",
            FakeChatCodexResponses,
        )

        from providers.codex_adapter import get_codex_pro_llm

        get_codex_pro_llm()

        assert calls[0]["model"] == "gpt-5.5"
        assert calls[0]["reasoning_effort"] == "xhigh"
        assert calls[0]["request_timeout"] == 180
        assert calls[0]["max_tokens"] == 10000
        assert calls[0]["temperature"] == 0.0

    def test_codex_reasoning_override_disables_reasoning(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        class FakeChatCodexResponses:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("providers.codex_adapter.resolve_codex_token", lambda: "t")
        monkeypatch.setattr(
            "providers.codex_adapter.settings",
            MagicMock(
                CODEX_FLASH_MODEL="gpt-5.4-mini",
                CODEX_FLASH_REASONING_EFFORT="medium",
                CODEX_PRO_MODEL="gpt-5.5",
                CODEX_PRO_REASONING_EFFORT="xhigh",
            ),
        )
        monkeypatch.setattr(
            "providers.codex_responses_llm.ChatCodexResponses",
            FakeChatCodexResponses,
        )

        from providers.codex_adapter import (
            codex_reasoning_override,
            get_codex_flash_llm,
            get_codex_pro_llm,
        )

        with codex_reasoning_override(flash=None, pro=None):
            get_codex_flash_llm()
            get_codex_pro_llm()

        assert calls[0]["reasoning_effort"] is None
        assert calls[1]["reasoning_effort"] is None


# ---------------------------------------------------------------------------
# Gemini / Anthropic adapter temperature (V4.2 — deterministic scout & CIO)
# ---------------------------------------------------------------------------


class TestGeminiAdapterTemperature:
    """Test Gemini adapter uses temperature=0 for both tiers (V4.2)."""

    def test_gemini_flash_uses_zero_temperature(self, monkeypatch: pytest.MonkeyPatch):
        calls = []

        class FakeChatGoogleGenerativeAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("providers.gemini._get_api_key", lambda: "test-key")
        monkeypatch.setattr(
            "providers.gemini.settings",
            MagicMock(GEMINI_FLASH_MODEL="gemini-2.5-flash"),
        )
        monkeypatch.setattr(
            "providers.gemini.ChatGoogleGenerativeAI", FakeChatGoogleGenerativeAI
        )

        from providers.gemini import get_flash_llm

        get_flash_llm()

        assert calls[0]["temperature"] == 0.0

    def test_gemini_pro_uses_zero_temperature(self, monkeypatch: pytest.MonkeyPatch):
        calls = []

        class FakeChatGoogleGenerativeAI:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr("providers.gemini._get_api_key", lambda: "test-key")
        monkeypatch.setattr(
            "providers.gemini.settings",
            MagicMock(GEMINI_PRO_MODEL="gemini-2.5-pro"),
        )
        monkeypatch.setattr(
            "providers.gemini.ChatGoogleGenerativeAI", FakeChatGoogleGenerativeAI
        )

        from providers.gemini import get_pro_llm

        get_pro_llm()

        assert calls[0]["temperature"] == 0.0


class TestAnthropicAdapterTemperature:
    """Test Anthropic adapter uses temperature=0 for both tiers (V4.2)."""

    def test_anthropic_flash_uses_zero_temperature(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        calls = []

        class FakeChatAnthropic:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(
            "providers.anthropic_adapter.resolve_anthropic_token", lambda: "t"
        )
        monkeypatch.setattr(
            "providers.anthropic_adapter.settings",
            MagicMock(ANTHROPIC_FLASH_MODEL="claude-haiku"),
        )
        monkeypatch.setattr("langchain_anthropic.ChatAnthropic", FakeChatAnthropic)

        from providers.anthropic_adapter import get_anthropic_flash_llm

        get_anthropic_flash_llm()

        assert calls[0]["temperature"] == 0.0

    def test_anthropic_pro_uses_zero_temperature(self, monkeypatch: pytest.MonkeyPatch):
        calls = []

        class FakeChatAnthropic:
            def __init__(self, **kwargs):
                calls.append(kwargs)

        monkeypatch.setattr(
            "providers.anthropic_adapter.resolve_anthropic_token", lambda: "t"
        )
        monkeypatch.setattr(
            "providers.anthropic_adapter.settings",
            MagicMock(ANTHROPIC_PRO_MODEL="claude-sonnet"),
        )
        monkeypatch.setattr("langchain_anthropic.ChatAnthropic", FakeChatAnthropic)

        from providers.anthropic_adapter import get_anthropic_pro_llm

        get_anthropic_pro_llm()

        assert calls[0]["temperature"] == 0.0


# ---------------------------------------------------------------------------
# Debate timeout selection
# ---------------------------------------------------------------------------


class TestDebateTimeoutSelection:
    """Test provider-aware debate timeout defaults."""

    def test_codex_xhigh_uses_extended_timeout(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "services.debate_chamber.settings",
            MagicMock(
                DEFAULT_LLM_PROVIDER="codex",
                CODEX_PRO_REASONING_EFFORT="xhigh",
                CODEX_FLASH_REASONING_EFFORT="medium",
                DEBATE_TIMEOUT_SECONDS=300,
                CODEX_DEBATE_TIMEOUT_SECONDS=900,
            ),
        )
        from services.debate_chamber import DebateChamber

        assert DebateChamber._default_timeout_seconds() == 900

    def test_gemini_keeps_base_timeout(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "services.debate_chamber.settings",
            MagicMock(
                DEFAULT_LLM_PROVIDER="gemini",
                CODEX_PRO_REASONING_EFFORT="xhigh",
                CODEX_FLASH_REASONING_EFFORT="medium",
                DEBATE_TIMEOUT_SECONDS=300,
                CODEX_DEBATE_TIMEOUT_SECONDS=900,
            ),
        )
        from services.debate_chamber import DebateChamber

        assert DebateChamber._default_timeout_seconds() == 300

    def test_codex_reasoning_override_keeps_base_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(
            "services.debate_chamber.settings",
            MagicMock(
                DEFAULT_LLM_PROVIDER="codex",
                CODEX_PRO_REASONING_EFFORT="xhigh",
                CODEX_FLASH_REASONING_EFFORT="medium",
                DEBATE_TIMEOUT_SECONDS=300,
                CODEX_DEBATE_TIMEOUT_SECONDS=900,
            ),
        )
        from providers.codex_adapter import codex_reasoning_override
        from services.debate_chamber import DebateChamber

        with codex_reasoning_override(flash=None, pro=None):
            assert DebateChamber._default_timeout_seconds() == 300


# ---------------------------------------------------------------------------
# OAuth token format detection (Anthropic adapter)
# ---------------------------------------------------------------------------


class TestOAuthTokenDetection:
    """Test _is_oauth_token() from anthropic_adapter."""

    def test_regular_api_key_is_not_oauth(self):
        from providers.anthropic_adapter import _is_oauth_token

        assert _is_oauth_token("sk-ant-api03-abc123") is False

    def test_setup_token_is_oauth(self):
        from providers.anthropic_adapter import _is_oauth_token

        assert _is_oauth_token("sk-ant-oat-abc123") is True

    def test_jwt_is_oauth(self):
        from providers.anthropic_adapter import _is_oauth_token

        assert _is_oauth_token("eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9") is True

    def test_cc_prefix_is_oauth(self):
        from providers.anthropic_adapter import _is_oauth_token

        assert _is_oauth_token("cc-abc123def456") is True

    def test_empty_is_not_oauth(self):
        from providers.anthropic_adapter import _is_oauth_token

        assert _is_oauth_token("") is False

    def test_random_key_is_not_oauth(self):
        from providers.anthropic_adapter import _is_oauth_token

        assert _is_oauth_token("random-api-key-123") is False


# ---------------------------------------------------------------------------
# Failure taxonomy — provider-specific errors
# ---------------------------------------------------------------------------


class TestFailureTaxonomy:
    """Test that failure_taxonomy correctly classifies provider-specific errors."""

    def test_oauth_expired_is_auth(self):
        from core.failure_taxonomy import classify_exception

        exc = Exception("Token expired: 401 Unauthorized")
        record = classify_exception(exc, source="anthropic")
        assert record.code.value == "AUTH"
        assert record.retryable is False

    def test_rate_limit_is_quota(self):
        from core.failure_taxonomy import classify_exception

        exc = Exception("rate_limit_exceeded: too many requests (429)")
        record = classify_exception(exc, source="codex")
        assert record.code.value == "QUOTA"
        assert record.retryable is True

    def test_overloaded_is_quota(self):
        from core.failure_taxonomy import classify_exception

        exc = Exception("overloaded: API is temporarily overloaded")
        record = classify_exception(exc, source="anthropic")
        assert record.code.value == "QUOTA"
        assert record.retryable is True

    def test_invalid_grant_is_auth(self):
        from core.failure_taxonomy import classify_exception

        exc = Exception("invalid_grant: refresh token expired")
        record = classify_exception(exc, source="codex")
        assert record.code.value == "AUTH"
        assert record.retryable is False


# ---------------------------------------------------------------------------
# Async-scoped provider override isolation
# ---------------------------------------------------------------------------


class TestAsyncProviderIsolation:
    """Test that ContextVar provider override is task-scoped."""

    @pytest.mark.asyncio
    async def test_concurrent_override_isolation(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "providers.llm_factory.settings",
            MagicMock(DEFAULT_LLM_PROVIDER="gemini"),
        )
        from providers.llm_factory import _resolve_provider, llm_provider_override

        results: dict[str, str] = {}

        async def task_a():
            with llm_provider_override("anthropic"):
                await asyncio.sleep(0.05)
                results["a"] = _resolve_provider()

        async def task_b():
            with llm_provider_override("codex"):
                await asyncio.sleep(0.05)
                results["b"] = _resolve_provider()

        await asyncio.gather(task_a(), task_b())
        assert results["a"] == "anthropic"
        assert results["b"] == "codex"
