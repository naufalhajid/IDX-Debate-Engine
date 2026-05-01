import os

from phi.llm.ollama import Ollama
from phi.llm.openai import OpenAIChat
from langchain_google_genai import ChatGoogleGenerativeAI

from core.settings import settings
from providers.gemini import _get_api_key


def _normalize_provider(provider: str | None) -> str:
    provider_name = (provider or settings.LLM_PROVIDER or "gemini").strip().lower()
    if provider_name == "qwen":
        return "ollama"
    return provider_name


def _create_gemini_llm(model: str, temperature: float, max_tokens: int, timeout: int):
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=_get_api_key(),
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
    )


def _create_ollama_llm(model: str, temperature: float, tier: str):
    llm = Ollama(model=model, temperature=temperature)
    setattr(llm, "tier", tier)
    return llm


def _create_openai_llm(model: str, temperature: float, tier: str):
    llm = OpenAIChat(model=model, temperature=temperature)
    setattr(llm, "tier", tier)
    return llm


def get_flash_llm(provider: str | None = None, model: str | None = None):
    provider_name = _normalize_provider(provider)
    model_name = model or settings.LLM_FLASH_MODEL

    if provider_name == "gemini":
        return _create_gemini_llm(
            model=model_name,
            temperature=0.1,
            max_tokens=4000,
            timeout=60,
        )
    if provider_name == "ollama":
        return _create_ollama_llm(model_name, temperature=0.1, tier="flash")
    if provider_name == "openai":
        return _create_openai_llm(model_name, temperature=0.1, tier="flash")

    raise ValueError(
        f"Unsupported LLM provider: {provider_name}. Supported providers: gemini, qwen, ollama, openai."
    )


def get_pro_llm(provider: str | None = None, model: str | None = None):
    provider_name = _normalize_provider(provider)
    model_name = model or settings.LLM_PRO_MODEL

    if provider_name == "gemini":
        return _create_gemini_llm(
            model=model_name,
            temperature=0.3,
            max_tokens=10000,
            timeout=90,
        )
    if provider_name == "ollama":
        return _create_ollama_llm(model_name, temperature=0.3, tier="pro")
    if provider_name == "openai":
        return _create_openai_llm(model_name, temperature=0.3, tier="pro")

    raise ValueError(
        f"Unsupported LLM provider: {provider_name}. Supported providers: gemini, qwen, ollama, openai."
    )
