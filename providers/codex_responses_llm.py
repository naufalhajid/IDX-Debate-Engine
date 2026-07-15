"""providers/codex_responses_llm.py — Custom Chat Model for OpenAI Responses API.

This overrides the standard LangChain BaseChatModel to communicate with
OpenAI's Responses API (e.g. ChatGPT Plus tokens via Device Code Flow),
instead of the standard Developer API (chat/completions).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from openai import AsyncOpenAI, OpenAI
from pydantic import SecretStr, PrivateAttr, field_validator

from utils.logger_config import logger
from utils.secret_redaction import redact_secrets

REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


class ChatCodexResponses(BaseChatModel):
    """Custom LangChain Chat Model for the OpenAI Responses API."""

    model: str
    api_key: SecretStr
    request_timeout: int = 60
    temperature: Optional[float] = None
    max_completion_tokens: Optional[int] = None
    max_tokens: Optional[int] = None
    reasoning_effort: Optional[str] = None
    base_url: Optional[str] = None
    credential_type: str = "oauth"

    _client: Any = PrivateAttr()
    _sync_client: Any = PrivateAttr()

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_reasoning_effort(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        effort = value.strip().lower()
        if not effort:
            return None
        if effort not in REASONING_EFFORTS:
            raise ValueError(
                "reasoning_effort must be one of "
                "none, minimal, low, medium, high, xhigh. "
                "Use 'xhigh' for Extra High."
            )
        return effort

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        self._rebuild_clients()

    def _rebuild_clients(self) -> None:
        default_base_url = (
            "https://api.openai.com/v1"
            if self.credential_type == "managed_api_key"
            else "https://chatgpt.com/backend-api/codex"
        )
        base_url = self.base_url or default_base_url
        self._client = AsyncOpenAI(
            api_key=self.api_key.get_secret_value(),
            base_url=base_url,
            timeout=self.request_timeout,
        )
        self._sync_client = OpenAI(
            api_key=self.api_key.get_secret_value(),
            base_url=base_url,
            timeout=self.request_timeout,
        )

    def _recover_auth_sync(self, exc: Exception) -> None:
        from providers.oauth_manager import (
            CodexAuthRecoveryExhausted,
            codex_token_fingerprint,
            get_codex_credential_type,
            is_codex_auth_expiry_error,
            recover_codex_token_after_auth_failure,
        )

        if not is_codex_auth_expiry_error(exc):
            raise exc
        if self.credential_type == "managed_api_key":
            raise CodexAuthRecoveryExhausted(
                "Managed Codex API key was rejected; automatic OAuth recovery "
                "is unavailable."
            ) from exc
        rejected_fingerprint = codex_token_fingerprint(
            self.api_key.get_secret_value()
        )
        try:
            recovered_token = recover_codex_token_after_auth_failure(
                rejected_token_fingerprint=rejected_fingerprint
            )
        except Exception as recovery_exc:
            raise CodexAuthRecoveryExhausted(
                "Codex request failed after one credential recovery attempt."
            ) from recovery_exc
        self.api_key = SecretStr(recovered_token)
        self.credential_type = get_codex_credential_type(recovered_token)
        self._rebuild_clients()

    async def _recover_auth_async(self, exc: Exception) -> None:
        await asyncio.to_thread(self._recover_auth_sync, exc)

    @property
    def _llm_type(self) -> str:
        return "chat-codex-responses"

    def _format_messages(
        self, messages: List[BaseMessage]
    ) -> tuple[str, List[Dict[str, Any]]]:
        """Convert LangChain messages into the Responses API input shape."""
        instructions = ""
        inputs = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                instructions += str(msg.content) + "\n"
            elif isinstance(msg, HumanMessage):
                content = msg.content
                if isinstance(content, str):
                    inputs.append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": content}],
                        }
                    )
                else:
                    # Handle multimodal if needed, simplify to text for now
                    inputs.append(
                        {
                            "role": "user",
                            "content": [{"type": "input_text", "text": str(content)}],
                        }
                    )
            elif isinstance(msg, AIMessage):
                if msg.content:
                    inputs.append(
                        {
                            "role": "assistant",
                            "content": [
                                {"type": "output_text", "text": str(msg.content)}
                            ],
                        }
                    )
                if getattr(msg, "tool_calls", None):
                    for tc in msg.tool_calls:
                        inputs.append(
                            {
                                "type": "function_call",
                                "call_id": tc["id"],
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"]),
                            }
                        )
            elif isinstance(msg, ToolMessage):
                inputs.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.tool_call_id,
                        "output": str(msg.content),
                    }
                )
        return instructions.strip(), inputs

    def _build_api_kwargs(
        self, messages: List[BaseMessage], **kwargs: Any
    ) -> Dict[str, Any]:
        instructions, input_items = self._format_messages(messages)

        api_kwargs = {
            "model": self.model,
            "input": input_items,
            "stream": True,  # Always use streaming as per Hermes robustness logic
            "store": False,  # Required by Codex backend
        }
        if not instructions:
            instructions = "You are a helpful AI assistant."
        api_kwargs["instructions"] = instructions

        if self.reasoning_effort:
            api_kwargs["reasoning"] = {"effort": self.reasoning_effort}

        tools = kwargs.get("tools")
        if tools:
            converted_tools = []
            for tool in tools:
                if isinstance(tool, dict):
                    if "function" in tool and isinstance(tool["function"], dict):
                        fn = tool["function"]
                        converted_tools.append(
                            {
                                "type": "function",
                                "name": fn.get("name"),
                                "description": fn.get("description", ""),
                                "strict": fn.get("strict", False),
                                "parameters": fn.get(
                                    "parameters", {"type": "object", "properties": {}}
                                ),
                            }
                        )
                    else:
                        name = tool.get("name")
                        if name:
                            converted_tools.append(
                                {
                                    "type": "function",
                                    "name": name,
                                    "description": tool.get("description", ""),
                                    "strict": tool.get("strict", False),
                                    "parameters": tool.get(
                                        "parameters",
                                        {"type": "object", "properties": {}},
                                    ),
                                }
                            )
            api_kwargs["tools"] = converted_tools

        return api_kwargs

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """Stream response asynchronously."""
        api_kwargs = self._build_api_kwargs(messages, **kwargs)

        try:
            stream = await self._client.responses.create(**api_kwargs)
        except Exception as exc:
            from providers.oauth_manager import (
                CodexAuthRecoveryExhausted,
                is_codex_auth_expiry_error,
            )

            if not is_codex_auth_expiry_error(exc):
                logger.error(
                    "[Codex Responses] Connection failed: {}",
                    redact_secrets(exc),
                )
                raise
            await self._recover_auth_async(exc)
            try:
                stream = await self._client.responses.create(**api_kwargs)
            except Exception as retry_exc:
                if is_codex_auth_expiry_error(retry_exc):
                    raise CodexAuthRecoveryExhausted(
                        "Codex request still unauthorized after one credential "
                        "recovery and replay."
                    ) from retry_exc
                logger.error(
                    "[Codex Responses] Replay failed: {}",
                    redact_secrets(retry_exc),
                )
                raise

        async for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "error":
                raise RuntimeError(
                    f"Responses API error: {getattr(event, 'message', '')}"
                )

            if "output_text.delta" in event_type:
                delta = getattr(event, "delta", "")
                if delta:
                    chunk = AIMessageChunk(content=delta)
                    if run_manager:
                        await run_manager.on_llm_new_token(delta)
                    yield ChatGenerationChunk(message=chunk)

            if event_type == "response.output_item.done":
                item = getattr(event, "item", {})
                itype = (
                    item.get("type")
                    if isinstance(item, dict)
                    else getattr(item, "type", None)
                )
                if itype == "function_call":
                    name = (
                        item.get("name", "")
                        if isinstance(item, dict)
                        else getattr(item, "name", "")
                    )
                    arguments = (
                        item.get("arguments", "{}")
                        if isinstance(item, dict)
                        else getattr(item, "arguments", "{}")
                    )
                    call_id = (
                        item.get("call_id", "")
                        if isinstance(item, dict)
                        else getattr(item, "call_id", "")
                    )

                    tool_chunk = {
                        "name": name,
                        "args": arguments,
                        "id": call_id,
                        "index": 0,
                    }
                    chunk = AIMessageChunk(content="", tool_call_chunks=[tool_chunk])
                    yield ChatGenerationChunk(message=chunk)

            if event_type in {"response.completed", "response.failed"}:
                resp_obj = getattr(event, "response", {})
                if getattr(resp_obj, "error", None) or (
                    isinstance(resp_obj, dict) and resp_obj.get("error")
                ):
                    raise RuntimeError(f"Terminal error: {resp_obj}")
                break

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate full response asynchronously."""
        content = ""
        tool_calls = []

        async for chunk in self._astream(messages, stop, run_manager, **kwargs):
            msg_chunk = chunk.message
            if isinstance(msg_chunk, AIMessageChunk):
                content += str(msg_chunk.content)
                if msg_chunk.tool_call_chunks:
                    for tc in msg_chunk.tool_call_chunks:
                        try:
                            args = json.loads(tc["args"])
                        except (json.JSONDecodeError, KeyError):
                            args = {}
                        tool_calls.append(
                            {
                                "id": tc["id"],
                                "name": tc["name"],
                                "args": args,
                            }
                        )

        ai_msg = AIMessage(content=content, tool_calls=tool_calls)
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """Stream response synchronously."""
        api_kwargs = self._build_api_kwargs(messages, **kwargs)

        try:
            stream = self._sync_client.responses.create(**api_kwargs)
        except Exception as exc:
            from providers.oauth_manager import (
                CodexAuthRecoveryExhausted,
                is_codex_auth_expiry_error,
            )

            if not is_codex_auth_expiry_error(exc):
                logger.error(
                    "[Codex Responses] Connection failed: {}",
                    redact_secrets(exc),
                )
                raise
            self._recover_auth_sync(exc)
            try:
                stream = self._sync_client.responses.create(**api_kwargs)
            except Exception as retry_exc:
                if is_codex_auth_expiry_error(retry_exc):
                    raise CodexAuthRecoveryExhausted(
                        "Codex request still unauthorized after one credential "
                        "recovery and replay."
                    ) from retry_exc
                logger.error(
                    "[Codex Responses] Replay failed: {}",
                    redact_secrets(retry_exc),
                )
                raise

        for event in stream:
            event_type = getattr(event, "type", "")

            if event_type == "error":
                raise RuntimeError(
                    f"Responses API error: {getattr(event, 'message', '')}"
                )

            if "output_text.delta" in event_type:
                delta = getattr(event, "delta", "")
                if delta:
                    chunk = AIMessageChunk(content=delta)
                    if run_manager:
                        run_manager.on_llm_new_token(delta)
                    yield ChatGenerationChunk(message=chunk)

            if event_type == "response.output_item.done":
                item = getattr(event, "item", {})
                itype = (
                    item.get("type")
                    if isinstance(item, dict)
                    else getattr(item, "type", None)
                )
                if itype == "function_call":
                    name = (
                        item.get("name", "")
                        if isinstance(item, dict)
                        else getattr(item, "name", "")
                    )
                    arguments = (
                        item.get("arguments", "{}")
                        if isinstance(item, dict)
                        else getattr(item, "arguments", "{}")
                    )
                    call_id = (
                        item.get("call_id", "")
                        if isinstance(item, dict)
                        else getattr(item, "call_id", "")
                    )

                    tool_chunk = {
                        "name": name,
                        "args": arguments,
                        "id": call_id,
                        "index": 0,
                    }
                    chunk = AIMessageChunk(content="", tool_call_chunks=[tool_chunk])
                    yield ChatGenerationChunk(message=chunk)

            if event_type in {"response.completed", "response.failed"}:
                resp_obj = getattr(event, "response", {})
                if getattr(resp_obj, "error", None) or (
                    isinstance(resp_obj, dict) and resp_obj.get("error")
                ):
                    raise RuntimeError(f"Terminal error: {resp_obj}")
                break

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Generate full response synchronously."""
        content = ""
        tool_calls = []

        for chunk in self._stream(messages, stop, run_manager, **kwargs):
            msg_chunk = chunk.message
            if isinstance(msg_chunk, AIMessageChunk):
                content += str(msg_chunk.content)
                if msg_chunk.tool_call_chunks:
                    for tc in msg_chunk.tool_call_chunks:
                        try:
                            args = json.loads(tc["args"])
                        except (json.JSONDecodeError, KeyError):
                            args = {}
                        tool_calls.append(
                            {
                                "id": tc["id"],
                                "name": tc["name"],
                                "args": args,
                            }
                        )

        ai_msg = AIMessage(content=content, tool_calls=tool_calls)
        return ChatResult(generations=[ChatGeneration(message=ai_msg)])
