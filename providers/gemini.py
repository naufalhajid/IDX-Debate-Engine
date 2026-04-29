import os

from langchain_google_genai import ChatGoogleGenerativeAI

from core.settings import settings


def _get_api_key() -> str:
    """Resolve Gemini API key.

    Settings is cached via @lru_cache at import time — before load_dotenv()
    runs in entry-point scripts. Fall back to os.environ which IS populated
    by the time factory functions are called at runtime.
    """

    return settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")


def get_flash_llm() -> ChatGoogleGenerativeAI:
    """Create a Gemini Flash instance — fast and cheap, for data extraction/summarization.

    max_tokens raised to 2000:
      Scout prompts request "3-4 technical paragraphs" which routinely need
      1200-1500 tokens. Truncation at 1500 cut off the last paragraph of
      multi-section reports, breaking downstream regex (e.g. FAIR VALUE: Rp X).
      2000 gives a safe headroom while staying well below the Flash limit.

    request_timeout = 60s:
      Without a timeout, a slow Gemini response causes the httpx connection
      to hang indefinitely, eventually triggering asyncio.CancelledError which
      propagates up through the entire pipeline.  A 60-second timeout allows
      tenacity to catch the error and retry cleanly instead.
    """

    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_FLASH_MODEL,
        google_api_key=_get_api_key(),
        temperature=0.1,
        max_tokens=4000,
        request_timeout=60,
    )


def get_pro_llm() -> ChatGoogleGenerativeAI:
    """Create a Gemini Pro instance — high reasoning capability, for debate and judgement.

    max_tokens raised to 2500:
      Previous value of 1100 was the primary cause of empty/truncated debate
      content.  Bull/Bear rounds request up to 1000 tokens of analysis, and
      the CIO JSON verdict (15+ fields, weighted_reasoning, summary, catalysts,
      risks) requires 800-1200 tokens by itself.  At 1100 the model frequently
      truncated mid-sentence or mid-JSON, producing empty debate_history entries
      and confidence=0.0 fallback verdicts.
      2500 comfortably fits a full debate round OR a complete CIOVerdict JSON.

    request_timeout = 90s:
      Pro reasoning calls are slower than Flash.  90s allows the model time
      to complete complex synthesis before tenacity triggers a retry.
    """

    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_PRO_MODEL,
        google_api_key=_get_api_key(),
        temperature=0.3,
        max_tokens=10000,
        request_timeout=90,
    )