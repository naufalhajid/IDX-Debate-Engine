"""Single-agent baseline analyzer for academic comparison.

The baseline uses the same compact ContextPack surface as the debate chamber,
but it asks one model for one final decision. There are no debate rounds,
opposing agents, or consensus mechanics in this module.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, Literal

import pandas as pd
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ConfigDict, Field

from providers.gemini import _get_api_key
from services.context_pack_builder import (
    ContextPack,
    build_context_pack,
    pack_to_prompt_string,
)
from services.fair_value_calculator import build_fair_value_payload
from utils.logger_config import logger
from utils.market_data_cache import derive_current_price, prefetch_market_data
from utils.technicals import compute_atr, compute_rsi


BASE_URL = "https://exodus.stockbit.com"
StockbitApiClient = None


def _get_stockbit_api_client_class():
    global StockbitApiClient
    if StockbitApiClient is None:
        from services.stockbit_api_client import StockbitApiClient as client_class

        StockbitApiClient = client_class
    return StockbitApiClient


class SingleAgentVerdict(BaseModel):
    """One-shot investment verdict produced by the single-agent baseline."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    rating: Literal["BUY", "HOLD", "AVOID"]
    confidence: float = Field(ge=0.0, le=1.0)
    fair_value: float
    current_price: float
    entry_price_range: str
    target_price: float
    stop_loss: float
    risk_reward_ratio: float
    reasoning: str
    key_risks: list[str]
    key_catalysts: list[str]
    timeframe: str
    mode: str = "single_agent"
    model_used: str
    generated_at: str
    run_id: str
    data_sources: list[str]


class SingleAgentResult(BaseModel):
    """Runtime envelope for one single-agent analysis."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    run_id: str
    verdict: SingleAgentVerdict | None
    status: Literal["success", "failed", "timeout"]
    error: str | None
    duration_seconds: float
    context_tokens: int
    generated_at: str


class SingleAgentAnalyzer:
    """One-call single-agent baseline for thesis comparison."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        timeout_seconds: int = 120,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._stockbit_client: StockbitApiClient | None = None

    @property
    def stockbit_client(self) -> StockbitApiClient:
        """Create the Stockbit client lazily to keep module import side-effect free."""
        if self._stockbit_client is None:
            self._stockbit_client = _get_stockbit_api_client_class()()
        return self._stockbit_client

    def _fetch_market_data(self, ticker: str) -> ContextPack:
        """Fetch provider data and normalize it into the shared ContextPack model."""
        normalized = ticker.strip().upper()
        market_data = asyncio.run(prefetch_market_data(normalized))
        current_price = derive_current_price(market_data)
        technicals = self._build_technical_indicators(market_data)
        if current_price <= 0:
            current_price = float(technicals.get("current_price") or 0.0)

        fundamentals, fair_value = self._fetch_fundamentals(normalized, current_price)
        sentiment_summary = self._fetch_sentiment_summary(normalized)
        data_sources = ["yfinance"]
        if fundamentals:
            data_sources.append("stockbit")
        if sentiment_summary:
            data_sources.append("stockbit_social")

        context = build_context_pack(
            normalized,
            {
                "current_price": current_price,
                "fair_value_estimate": fair_value,
                "fundamentals": fundamentals,
                "technicals": technicals,
                "sentiment_summary": sentiment_summary,
                "data_sources": data_sources,
            },
        )
        if context.price <= 0:
            raise RuntimeError(f"Unable to derive current price for {normalized}")
        return context

    def _fetch_fair_value(
        self,
        ticker: str,
        context: ContextPack,
    ) -> float | None:
        """Extract fair value from the context fundamentals or top-level field."""
        for value in (
            context.fair_value,
            context.fundamentals.get("fair_value"),
            context.fundamentals.get("fair_value_estimate"),
        ):
            parsed = self._to_float(value)
            if parsed is not None:
                return parsed
        return None

    def _build_prompt(
        self,
        ticker: str,
        context: ContextPack,
        fair_value: float | None,
    ) -> str:
        """Build the one-shot prompt for the single-agent baseline."""
        context_pack_string = pack_to_prompt_string(context)
        fair_value_line = (
            f"\nFair Value Estimate: Rp {fair_value:,.0f}\n"
            if fair_value is not None
            else ""
        )
        return f"""
You are a senior investment analyst at a top-tier Indonesian investment bank.
Analyze the following stock data and provide a swing trade recommendation for
{ticker.strip().upper()} listed on Bursa Efek Indonesia.

{context_pack_string}
{fair_value_line}
Based on ALL available data above, provide your analysis and recommendation.

You must respond with ONLY a valid JSON object with exactly these fields:
{{
  "rating": "BUY" | "HOLD" | "AVOID",
  "confidence": <float 0.0-1.0>,
  "entry_price_range": "<low> - <high>",
  "target_price": <float>,
  "stop_loss": <float>,
  "risk_reward_ratio": <float>,
  "reasoning": "<comprehensive explanation max 500 chars>",
  "key_risks": ["<risk1>", "<risk2>", "<risk3>"],
  "key_catalysts": ["<catalyst1>", "<catalyst2>"],
  "timeframe": "1-3 Months"
}}

Rules:
- entry_price_range must be BELOW current price for BUY (wait for pullback)
- stop_loss must be below entry_price_range
- target_price must be above entry_price_range
- confidence reflects your certainty
- respond with JSON only, no other text
""".strip()

    async def _call_llm(self, prompt: str) -> str:
        """Call Gemini once and return the raw text response."""
        llm = ChatGoogleGenerativeAI(
            model=self.model,
            google_api_key=_get_api_key(),
            temperature=0.1,
            max_tokens=4000,
            request_timeout=self.timeout_seconds,
        )
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        content = getattr(response, "content", "")
        if not str(content).strip():
            raise RuntimeError("LLM returned an empty response")
        return str(content)

    def _parse_response(
        self,
        raw: str,
        ticker: str,
        context: ContextPack,
        fair_value: float | None,
        run_id: str,
        duration: float,
    ) -> SingleAgentResult:
        """Parse the model response into a typed single-agent result."""
        generated_at = datetime.now(timezone.utc).isoformat()
        normalized = ticker.strip().upper()
        try:
            parsed = json.loads(self._strip_json_fence(raw))
            verdict = SingleAgentVerdict(
                ticker=normalized,
                rating=str(parsed["rating"]).upper(),
                confidence=parsed["confidence"],
                fair_value=float(fair_value if fair_value is not None else 0.0),
                current_price=float(context.price),
                entry_price_range=str(parsed["entry_price_range"]),
                target_price=parsed["target_price"],
                stop_loss=parsed["stop_loss"],
                risk_reward_ratio=parsed["risk_reward_ratio"],
                reasoning=str(parsed["reasoning"])[:500],
                key_risks=list(parsed.get("key_risks") or []),
                key_catalysts=list(parsed.get("key_catalysts") or []),
                timeframe=str(parsed.get("timeframe") or "1-3 Months"),
                mode="single_agent",
                model_used=self.model,
                generated_at=generated_at,
                run_id=run_id,
                data_sources=list(context.data_sources),
            )
            return SingleAgentResult(
                ticker=normalized,
                run_id=run_id,
                verdict=verdict,
                status="success",
                error=None,
                duration_seconds=duration,
                context_tokens=context.token_estimate,
                generated_at=generated_at,
            )
        except Exception as exc:
            return self._failure_result(
                ticker=normalized,
                run_id=run_id,
                status="failed",
                error=str(exc),
                duration=duration,
                context_tokens=context.token_estimate,
            )

    async def analyze(self, ticker: str, run_id: str) -> SingleAgentResult:
        """Run the full one-call single-agent baseline pipeline."""
        start = time.monotonic()
        normalized = ticker.strip().upper()
        context_tokens = 0
        try:
            context = await asyncio.to_thread(self._fetch_market_data, normalized)
            fair_value = self._fetch_fair_value(normalized, context)
            prompt = self._build_prompt(normalized, context, fair_value)
            context_tokens = len(prompt) // 4
            raw = await asyncio.wait_for(
                self._call_llm(prompt),
                timeout=self.timeout_seconds,
            )
            result = self._parse_response(
                raw,
                normalized,
                context,
                fair_value,
                run_id,
                time.monotonic() - start,
            )
            return result.model_copy(update={"context_tokens": context_tokens})
        except asyncio.TimeoutError:
            return self._failure_result(
                ticker=normalized,
                run_id=run_id,
                status="timeout",
                error="LLM call timed out",
                duration=time.monotonic() - start,
                context_tokens=context_tokens,
            )
        except Exception as exc:
            return self._failure_result(
                ticker=normalized,
                run_id=run_id,
                status="failed",
                error=str(exc),
                duration=time.monotonic() - start,
                context_tokens=context_tokens,
            )

    async def analyze_batch(
        self,
        tickers: list[str],
        run_id: str,
    ) -> list[SingleAgentResult]:
        """Analyze tickers sequentially, preserving per-ticker failures."""
        results: list[SingleAgentResult] = []
        for ticker in tickers:
            normalized = ticker.strip().upper()
            logger.info(f"[SingleAgent] Starting {normalized}")
            try:
                result = await self.analyze(normalized, run_id)
            except Exception as exc:
                result = self._failure_result(
                    ticker=normalized,
                    run_id=run_id,
                    status="failed",
                    error=str(exc),
                    duration=0.0,
                    context_tokens=0,
                )
            results.append(result)
            if result.verdict is not None:
                logger.info(
                    f"[SingleAgent] {normalized}: "
                    f"{result.verdict.rating} "
                    f"conf={result.verdict.confidence:.0%}"
                )
            else:
                logger.warning(f"[SingleAgent] {normalized}: {result.status}")
        return results

    def _fetch_fundamentals(
        self,
        ticker: str,
        current_price: float,
    ) -> tuple[dict[str, Any], float | None]:
        try:
            raw = self.stockbit_client.get(
                f"{BASE_URL}/keystats/ratio/v1/{ticker}?year_limit=10"
            )
        except Exception as exc:
            logger.warning(
                f"[SingleAgent] Fundamental fetch failed for {ticker}: {exc}"
            )
            return {"brief": f"Fundamental data unavailable: {exc}"}, None

        if not raw:
            return {"brief": "Fundamental data unavailable"}, None

        report, fv_payload = build_fair_value_payload(raw, ticker, current_price)
        fair_value = fv_payload.get("fair_value")
        return {
            "brief": report,
            "fair_value": fair_value,
            "fair_value_base": fv_payload.get("fair_value_base"),
            "fair_value_low": fv_payload.get("fair_value_low"),
            "fair_value_high": fv_payload.get("fair_value_high"),
            "range_pct": fv_payload.get("range_pct"),
            "risk_overvalued": fv_payload.get("risk_overvalued"),
            "valuation_band_context": fv_payload.get("valuation_band_context"),
        }, fair_value

    def _fetch_sentiment_summary(self, ticker: str) -> str | None:
        try:
            raw = self.stockbit_client.get(
                f"{BASE_URL}/stream/v3/symbol/{ticker}/pinned"
            )
        except Exception as exc:
            logger.warning(f"[SingleAgent] Sentiment fetch failed for {ticker}: {exc}")
            return f"Sentiment data unavailable: {exc}"
        if not raw:
            return "Sentiment data unavailable"
        return json.dumps(raw, ensure_ascii=False, separators=(",", ":"))[:5_000]

    def _build_technical_indicators(
        self, market_data: dict[str, Any]
    ) -> dict[str, Any]:
        history = market_data.get("history")
        if history is None or len(history) == 0:
            return {"current_price": derive_current_price(market_data)}

        try:
            frame = history.copy()
            if isinstance(getattr(frame, "columns", None), pd.MultiIndex):
                frame.columns = frame.columns.get_level_values(0)

            close = frame["Close"].dropna().squeeze()
            high = frame["High"].dropna().squeeze()
            low = frame["Low"].dropna().squeeze()
            volume = frame["Volume"].dropna().squeeze()
            current_price = float(close.iloc[-1])

            indicators: dict[str, Any] = {
                "current_price": round(current_price, 0),
                "52w_high": round(float(close.max()), 0),
                "52w_low": round(float(close.min()), 0),
            }
            if len(close) >= 20:
                indicators["sma20"] = round(float(close.rolling(20).mean().iloc[-1]), 0)
                indicators["ema20"] = round(
                    float(close.ewm(span=20, adjust=False).mean().iloc[-1]),
                    0,
                )
                indicators["avg_volume_20d"] = round(float(volume.tail(20).mean()), 0)
            if len(close) >= 50:
                indicators["ma50"] = round(float(close.rolling(50).mean().iloc[-1]), 0)
                indicators["ma200"] = round(
                    float(close.rolling(window=200, min_periods=50).mean().iloc[-1]),
                    0,
                )
            if len(close) >= 14:
                indicators["rsi14"] = round(float(compute_rsi(close).iloc[-1]), 1)
                indicators["atr14"] = round(
                    float(compute_atr(high, low, close).iloc[-1]), 0
                )
            return indicators
        except Exception as exc:
            logger.warning(f"[SingleAgent] Technical indicator build failed: {exc}")
            return {"current_price": derive_current_price(market_data)}

    @staticmethod
    def _strip_json_fence(raw: str) -> str:
        from services.debate_chamber import DebateChamber

        return DebateChamber._sanitize_json(raw)

    @staticmethod
    def _to_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(str(value).replace(",", ""))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _failure_result(
        *,
        ticker: str,
        run_id: str,
        status: Literal["failed", "timeout"],
        error: str | None,
        duration: float,
        context_tokens: int,
    ) -> SingleAgentResult:
        return SingleAgentResult(
            ticker=ticker,
            run_id=run_id,
            verdict=None,
            status=status,
            error=error,
            duration_seconds=duration,
            context_tokens=context_tokens,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )


DEFAULT_ANALYZER = SingleAgentAnalyzer()


async def _run_cli(args: argparse.Namespace) -> None:
    result = await DEFAULT_ANALYZER.analyze(
        ticker=args.ticker,
        run_id=args.run_id,
    )
    print(result.model_dump_json(indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run single-agent stock analysis.")
    parser.add_argument("--ticker", required=True, help="IDX ticker, e.g. BBCA")
    parser.add_argument("--run-id", default="single-agent-cli", help="Run identifier")
    args = parser.parse_args()
    asyncio.run(_run_cli(args))


if __name__ == "__main__":
    main()
