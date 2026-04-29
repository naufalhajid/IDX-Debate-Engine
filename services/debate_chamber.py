"""
debate_chamber.py — Production-grade LangGraph multi-agent stock debate system.

Phase 1: Parallel Orchestration  — Fundamental / Chartist / Sentiment run concurrently.
Phase 2: Anti-Groupthink Logic   — Round-aware prompts; R2 forbids repeating R1 data.
Phase 3: Adaptive Short-Circuit  — Consensus bypass + State Cleaner (context pruning).
Phase 4: Decisive CIO Judge      — Weighted synthesis, Confidence gate, Pydantic output.

Target market : IHSG (Indonesia)
Token budget  : 500 k tokens  →  Flash for data extraction, Pro for reasoning only.

Refactored (audit fixes):
  - Chartist ingests real OHLCV via yfinance; MA50/MA200/RSI/ATR pre-computed in Python
  - CIO receives a Python-computed Trade Envelope (entry/target/stop), does NOT invent prices
  - Bear R2 challenges Margin of Safety using ATR-based downside
  - Conflict Resolution Matrix enforced in CIO prompt
  - All prices snapped to valid IHSG tick sizes
"""

import asyncio
import json
import re
from typing import Literal

import pandas as pd
import yfinance as yf
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

try:
    import pytz
    _TZ_WIB = pytz.timezone("Asia/Jakarta")
except ImportError:
    from datetime import timezone, timedelta
    _TZ_WIB = timezone(timedelta(hours=7))

from core.budget import (
    BudgetExhaustedError,
    check_and_increment_flash_budget,
    check_and_increment_pro_budget,
)
from providers.gemini import get_flash_llm, get_pro_llm
from core.settings import settings
from schemas.debate import CIOVerdict, DebateChamberState, DebateMessage, validate_swing_targets
from services.stockbit_api_client import StockbitApiClient
from services.fair_value_calculator import build_fair_value_report
from utils.logger_config import logger
from utils.technicals import compute_atr, compute_rsi, snap_to_tick


# ---------------------------------------------------------------------------
# Transient-error guard — retry only on genuinely-transient failures
# ---------------------------------------------------------------------------

#: Error signatures that are PERMANENT — never retry these.  Retrying a
#: bad API key or a billing failure just burns time while still failing.
_PERMANENT_ERROR_PATTERNS = (
    "invalid api key",
    "api key not valid",
    "authentication",
    "permission_denied",
    "permission denied",
    "billing",
    "safety",
    "prohibited_content",
    "quota_exceeded_forever",
)

#: Error signatures that ARE worth retrying (with exponential backoff).
_TRANSIENT_ERROR_PATTERNS = (
    "429",
    "503",
    "504",
    "resource exhausted",
    "deadline exceeded",
    "unavailable",
    "connection reset",
    "connection aborted",
    "connection dropped",  # wraps asyncio.CancelledError from network timeout
    "timeout",
    "empty response",      # Gemini safety filter / token budget returns empty content
)


def _is_transient_error(exc: BaseException) -> bool:
    """Return True only if ``exc`` is safe to retry.

    Budget exhaustion is never transient — the caller should propagate
    it and abort.  Permanent errors (bad key, billing, safety blocks)
    are likewise never retried to prevent wasted calls.
    """

    if isinstance(exc, BudgetExhaustedError):
        return False
    s = str(exc).lower()
    if any(p in s for p in _PERMANENT_ERROR_PATTERNS):
        return False
    return any(t in s for t in _TRANSIENT_ERROR_PATTERNS)


# ---------------------------------------------------------------------------
# Internal schemas
# ---------------------------------------------------------------------------

class ConsensusSchema(BaseModel):
    consensus_reached: bool = Field(
        description="True only if BOTH agents overwhelmingly agree on the same direction "
                    "with no major unresolved fundamental objections."
    )


# ---------------------------------------------------------------------------
# System Prompts — 5-agent roster
# ---------------------------------------------------------------------------

# ── Phase 1 Data Scouts (run on Flash — cheap, fast) ────────────────────────

FUNDAMENTAL_SCOUT_PROMPT = """\
You are a Fundamental Data Scout specializing in IHSG stocks for SWING TRADE analysis (1-3 month horizon).

PRIMARY MISSION — Calculate Fair Value:
  1. FAIR VALUE (most important): Your python system has pre-calculated the fair value.
     Locate the "FAIR VALUE REPORT" injected at the start of your data.
     Extract the value of "FAIR VALUE (weighted avg)". Label it clearly: "FAIR VALUE: Rp X,XXX".
  2. VALUATION VERDICT: Is the current price UNDERVALUED, FAIRLY VALUED, or OVERVALUED vs fair value?
     State the discount/premium as a percentage.
  3. SUPPORT METRICS: ROE trend (3yr), Net Margin trend (3yr), Debt/Equity, Dividend Yield.
  4. GROWTH CATALYST: One specific upcoming event (earnings, ex-dividend, contract) within 1-3 months.

RULES: Numbers ONLY. No vague statements. Rupiah prices must be explicit. Max 1200 tokens. Write 3-4 compact technical paragraphs — do NOT pad for length."""

CHARTIST_PROMPT = """\
You are a Technical Chartist specializing in IHSG swing trade entry/exit timing (1-3 month frame).

CRITICAL: The PRE-COMPUTED TECHNICALS section below contains Python-calculated indicator values.
These are GROUND TRUTH — reference them directly. Do NOT recalculate any indicator.

PRIMARY MISSION — Interpret the Trade Setup using provided technical data:
  1. TREND CONTEXT: Using the provided MA50 and MA200 values, describe the current trend.
     State whether price is above/below these moving averages and what it means.
  2. ENTRY ZONE: Using the provided SMA20 and MA50, identify the nearest confirmed support.
     State as a range: "ENTRY ZONE: Rp X,XXX – Rp Y,YYY".
  3. TARGET PRICE: The nearest strong resistance level that would yield 3-10% gain from entry midpoint.
     State as: "TARGET: Rp Z,ZZZ (approx. X% from entry mid)".
  4. STOP-LOSS: Reference the ATR(14) value provided. A stop at 1.5× ATR below SMA20 is standard.
     State as: "STOP LOSS: Rp W,WWW (1.5× ATR below SMA20)".
  5. RSI INTERPRETATION: Using the provided RSI(14) value, assess momentum state.
  6. VOLUME SIGNAL: Is current volume confirming or denying the price move?

RULES: All prices in Rupiah. Use the pre-computed numbers as your foundation — do not
fabricate MA/RSI values. Max 1200 tokens. Write 3-4 compact technical paragraphs — do NOT pad for length."""

SENTIMENT_PROMPT = """\
You are a Sentiment Specialist monitoring Stockbit social signal data for IHSG swing trade timing.

Analyze the raw stream/social JSON and extract:
  • Overall mood: BULLISH / NEUTRAL / BEARISH with a % confidence estimate
  • Dominant discussion theme (e.g., dividend rumour, earnings miss concern)
  • Volume anomaly: Is discussion volume abnormally high or low vs baseline?
  • Swing-trade timing signal: Is sentiment at EXTREME (contrarian opportunity) or trending with price?
  • Red flags: Any coordinated pump signals, insider-leak language, or panic patterns?

RULES: Max 1200 tokens. Write 3-4 compact technical paragraphs. Be specific — note if sentiment is diverging from price action."""

# ── Phase 2 Debate Agents (run on Pro — only when reasoning needed) ──────────

BULL_SYSTEM_PROMPT_R1 = """\
You are a Senior Equity Analyst building the strongest possible swing trade BUY case (1-3 month horizon).

ROUND 1 OBJECTIVE — Build the Trade Thesis:
  1. FUNDAMENTAL FLOOR: Cite the fair value estimate and confirm current price is at a discount.
     If price is ABOVE fair value, you must explain why the momentum/catalyst still justifies entry.
  2. TECHNICAL ENTRY: Confirm the entry zone from Chartist data is a high-probability support.
     Cite the specific level (e.g., "MA50 at Rp 4,850 has held 3 times in 6 months").
  3. CATALYST: Name ONE specific event within 1-3 months that will drive the price to target.
  4. RISK/REWARD: State explicitly: "Entry Rp X → Target Rp Y → Stop Rp Z → R/R ratio: N:1"

RULES: Swing trade frame ONLY (1-3 months). No long-term narratives. Cite exact prices. Max 1000 tokens. Write 3-4 compact technical paragraphs."""

BULL_SYSTEM_PROMPT_R2 = """\
You are a Senior Equity Analyst in Cross-Examination mode — swing trade frame.

ROUND 2 OBJECTIVE — Defend Entry Timing Against the Bear:
  ⛔ DO NOT repeat ANY price level, ratio, or argument from your Round 1 response.
  ✅ Attack the Bear's specific stop-loss / target / valuation arguments.
  ✅ If the Bear said the support will break, name a secondary support below it that limits downside.
  ✅ If the Bear challenged the catalyst, provide corroborating evidence or a fallback catalyst.
  ✅ Address whether the current price-to-fair-value gap is wide enough to absorb the Bear's risk scenario.

RULES: No repeated data. Attack specific Bear arguments. Max 1000 tokens. Write 3-4 compact technical paragraphs."""

BEAR_SYSTEM_PROMPT_R1 = """\
You are a Forensic Financial Auditor building the strongest possible swing trade AVOID/SELL case.

ROUND 1 OBJECTIVE — Challenge the Trade Setup:
  1. OVERVALUATION CHECK: Is the current price above fair value? State the premium as a percentage.
     If it is, the swing trade entry has NO margin of safety — state this bluntly.
  2. TECHNICAL BREAKDOWN RISK: Is there a pattern (lower-highs, breakdown below MA50/MA200,
     bearish volume divergence) that makes the support level cited by the Bull unreliable?
     Cite exact price levels.
  3. CATALYST RISK: What could prevent the Bull's catalyst from materialising within 1-3 months?
  4. UNFAVOURABLE R/R: If the stop-loss is close to entry but resistance is far away, state the
     actual R/R ratio and explain why it makes the trade unattractive.

RULES: Cite exact prices to counter every Bull price level. Max 1000 tokens. Write 3-4 compact technical paragraphs."""

BEAR_SYSTEM_PROMPT_R2 = """\
You are a Forensic Financial Auditor in Cross-Examination mode — swing trade frame.

ROUND 2 OBJECTIVE — Destroy the Bull's Swing Setup:
  ⛔ DO NOT repeat ANY price level, ratio, or argument from your Round 1 response.
  ✅ Dismantle the Bull's specific entry zone, target, or catalyst claims from Round 1.
  ✅ If the Bull cited MA50 as support, show prior instances where MA50 failed for this stock.
  ✅ If the Bull cited a fundamental floor, show if the floor has drifted lower with declining earnings.
  ✅ Present an alternative price scenario: "If support breaks, next support is Rp X — making
     the actual max loss Rp Y, not Rp Z as the Bull assumes."
  ✅ MARGIN OF SAFETY STRESS TEST: If ATR(14) data is available in the technical summary,
     calculate the maximum 1-week adverse move (2 × ATR). Compare this to the Bull's claimed
     margin of safety. If 2×ATR wipes out the margin of safety, the trade is unviable
     for swing execution — state this explicitly.

RULES: No repeated data. Every counter-argument must cite a specific price. Max 1000 tokens. Write 3-4 compact technical paragraphs."""

# ── Adaptive nodes ───────────────────────────────────────────────────────────

CONSENSUS_PROMPT = """\
You are a Consensus Evaluator. Read the Bull and Bear arguments below.
Answer ONLY: do both agents overwhelmingly agree on the same investment direction
(e.g., both conclude HOLD or both conclude BUY) with NO major unresolved objections?

Return true only if consensus is genuine and unambiguous. Return false if there is
meaningful disagreement or if one side raised a critical unaddressed risk."""

STATE_CLEANER_PROMPT = """\
You are a Context Pruner for a swing trade debate. Compress the history below into:

BULL TRADE CASE (3-5 bullets — preserve ALL price numbers: entry zone, target, stop, fair value):
BEAR COUNTER-CASE (3-5 bullets — preserve ALL price numbers they challenged):
UNRESOLVED TENSION (1-2 sentences — the core price/timing disagreement):

Max 200 words. Every Rupiah price cited in the original MUST appear in the summary. Discard all filler."""

DEVILS_ADVOCATE_PROMPT = """\
You are the Devil's Advocate for IHSG swing trade analysis.
Challenge the trade setup with TWO specific questions:

1. MACRO/COMPANY RISK & FOREIGN FLOW: One specific scenario that could break the cited support level
   within 1-3 months. Explicitly inject Foreign Flow metrics if relevant (e.g. Net Foreign Sell 
   pressure) or Dividend Ex-Date traps.
   Example: "If this stock enters Ex-Date next month, or foreign funds accelerate dumping, 
   could the Rp 4,850 MA50 support break, triggering stops all the way to Rp 4,400?"

2. EXECUTION RISK: Can the projected return (3-10%) survive IHSG transaction costs
   (buy commission ~0.15%, sell commission ~0.25%, WHT on dividends 10%)?
   If the net return after costs drops below 2%, flag it as insufficient.

Format: Two direct questions, each under 60 words, each naming a specific price level."""

# ── CIO Judge — Swing Trade Edition (Phase 4) ───────────────────────────────

CIO_SYSTEM_PROMPT = """\
You are the Chief Investment Officer specializing in IHSG Swing Trading (1-3 month horizon, 3-10% target).

YOUR MANDATE — Validate the Python-Calculated Trade Plan:

IMPORTANT: The TRADE ENVELOPE below was calculated by Python using real market data.
You MUST use the provided entry, target, and stop-loss prices VERBATIM.
Do NOT invent or override these price levels. Your job is to APPROVE or REJECT the plan.

STEP 1 — FAIR VALUE:
  Read the fair value from the Trade Envelope. It is pre-calculated by Python.
  If current price is ABOVE fair value, you MUST flag this in weighted_reasoning and
  strongly consider a HOLD or AVOID rating — a negative margin of safety is the #1 swing trade killer.

STEP 2 — TRADE ENVELOPE VALIDATION:
  Review the Python-calculated entry/target/stop prices.
  Confirm they align with the debate findings.
  Use the pre-computed R/R ratio to guide your rating.

STEP 3 — CONFLICT RESOLUTION (MANDATORY):
  Read the CONFLICT RESOLUTION signal provided. Apply this strict matrix:
  • Fundamental ✅ + Technical ✅  → BUY (confidence ≥ 0.70)
  • Fundamental ✅ + Technical ❌  → HOLD ("Wait for technical confirmation")
  • Fundamental ❌ + Technical ✅  → If strongly positive Foreign Flow / Momentum with Volume breakout, Lean BUY (Momentum Play, size 50%). Otherwise, HOLD.
  • Fundamental ❌ + Technical ❌  → AVOID
  • Any ✅ + Sentiment EXTREME    → Lower confidence by 0.10 (contrarian caution)

STEP 4 — FINAL RATING RULES:
  • STRONG_BUY  : Price < Fair Value, R/R ≥ 2.0, clear catalyst, strong support confirmed.
  • BUY         : Price ≤ Fair Value, R/R ≥ 1.5, support holds.
  • HOLD        : Price near Fair Value OR R/R < 1.5 OR target < 3%.
  • AVOID       : Price > Fair Value (overvalued) OR R/R < 1.0 OR no clear catalyst.

STEP 5 — ADDRESS the Devil's Advocate scenario in your weighted_reasoning.
         If you cannot dismiss the DA scenario, lower your confidence score accordingly.

STEP 6 — Use the exact entry_price_range, target_price, stop_loss, and fair_value
         from the Trade Envelope. Do NOT change them.

CRITICAL OUTPUT FORMAT:
Respond ONLY with a valid JSON object. Do NOT include any text, explanation,
or markdown fences (``` or ```json) before or after the JSON object.
Your entire response must be parseable by json.loads() with no preprocessing."""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

def post_evaluator_router(
    state: DebateChamberState,
) -> Literal["devils_advocate", "state_cleaner"]:
    """
    Short-circuit: if consensus reached OR 2 rounds complete → go to CIO path.
    Otherwise → prune state and run another debate round.
    """
    if state.get("consensus_reached") or state["round_count"] >= 2:
        return "devils_advocate"
    return "state_cleaner"


# ---------------------------------------------------------------------------
# DebateChamber
# ---------------------------------------------------------------------------

BASE_URL = "https://exodus.stockbit.com"


class DebateChamber:
    """
    LangGraph multi-agent debate system for IHSG stock analysis.

    Graph topology
    ──────────────
    START ──fan-out──► fundamental ─┐
          ──fan-out──► chartist    ─┼──► synthesizer ──► bullish_analyst
          ──fan-out──► sentiment   ─┘                         │
                                                        bearish_auditor
                                                              │
                                                    consensus_evaluator
                                                         │         │
                                                  (agreed/r≥2)  (disagree)
                                                         │         │
                                                  devils_advocate  state_cleaner
                                                         │              │
                                                         │         bullish_analyst
                                                     cio_judge
                                                         │
                                                        END
    """

    def __init__(self, flash_llm=None, pro_llm=None, stockbit_client=None):
        self.flash_llm = flash_llm or get_flash_llm()
        self.pro_llm = pro_llm or get_pro_llm()
        self.stockbit_client = stockbit_client or StockbitApiClient()
        self.app = self._build_graph()

    # ── LLM & HTTP helpers ──────────────────────────────────────────────────

    def _classify_llm_tier(self, llm) -> str:
        """
        Determine whether this LLM instance is Pro or Flash so we can charge
        the right budget counter.

        `with_structured_output(...)` returns a wrapped Runnable that does
        not expose the `.model` attribute directly, so we fall back to
        introspecting the underlying bound LLM when possible.
        """
        model_name = getattr(llm, "model", None)
        if model_name is None:
            # `with_structured_output` wraps in a RunnableSequence; dig one
            # level deeper for best-effort detection.
            bound = getattr(llm, "bound", None) or getattr(llm, "first", None)
            model_name = getattr(bound, "model", None)
        if model_name is None:
            return "flash"  # safe default — under-count rather than over-count
        m = str(model_name).lower()
        if "pro" in m:
            return "pro"
        return "flash"

    async def _invoke_llm(self, llm, messages, inject_rules: bool = True):
        """
        Invoke LLM dengan budget guard dan global rules injection.

        Parameter inject_rules dihidupkan/dimatikan untuk memastikan
        structured output (CIO & Consensus) tidak berbenturan instruksi.
        """
        tier = self._classify_llm_tier(llm)
        if tier == "pro":
            await check_and_increment_pro_budget()
        else:
            await check_and_increment_flash_budget()

        msgs = list(messages)
        
        # FIX: Hanya suntikkan global rules jika inject_rules = True
        if inject_rules:
            from datetime import datetime
            current_date = datetime.now(_TZ_WIB).strftime("%Y-%m-%d")
            global_rules = f"""
GLOBAL RELIABILITY RULES (MANDATORY)
Current Date (Asia/Jakarta): {current_date}

1) TIME AWARENESS
- Treat any event date strictly relative to Current Date.
- If event_date < Current Date, label it as PAST_EVENT_NOT_CATALYST.
- Past events cannot be used as future catalysts for 1-3 month swing thesis.
- If date is ambiguous/unparseable, mark DATE_UNCERTAIN and reduce confidence.

2) NULL VS ZERO SEMANTICS
- "INSUFFICIENT_DATA", "N/A", missing, or unknown values must be represented as null, NEVER 0.
- Use 0 only when the true numeric value is explicitly zero in source data.
- Do not infer bankruptcy/zero-value from missing data.

3) CONSISTENCY CHECKS
- If verdict is AVOID or WAIT_AND_SEE due to missing/invalid core data, do not present active trade recommendation as final actionable call.
- If two metrics conflict across sections, explicitly explain likely source difference or mark NEEDS_RECONCILIATION.

4) OUTPUT DISCIPLINE
- Never fabricate dates, prices, or percentages.
- If critical fields are null, say so explicitly and lower confidence.
- Prioritize candor over completeness.
"""
            for i, msg in enumerate(msgs):
                if getattr(msg, "type", "") == "system":
                    msgs[i] = SystemMessage(content=f"{global_rules}\n\n{msg.content}")
                    break

        resp = await self._invoke_llm_with_retry(llm, msgs)
        return resp

    @retry(
        wait=wait_exponential(min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_transient_error),
    )
    async def _invoke_llm_with_retry(self, llm, messages):
        try:
            resp = await llm.ainvoke(messages)
        except asyncio.CancelledError:
            # CancelledError arises when the underlying HTTP connection is
            # dropped or timed-out by the network layer.  Wrap it in a
            # regular RuntimeError so tenacity treats it as a transient
            # failure and retries instead of propagating it to the event loop
            # (which would abort the entire pipeline run).
            raise RuntimeError("LLM request cancelled (connection dropped / timeout)")

        # ── Guard: detect empty or safety-filtered responses ─────────────────
        # Gemini sometimes returns an AIMessage with empty content when it
        # triggers a safety filter or hits an internal token issue.  It does
        # NOT raise an exception in these cases, so without this check the
        # empty string silently propagates into DebateMessage.content and the
        # CIO receives a debate with no arguments — producing confidence=0.0.
        content = getattr(resp, "content", None)
        if not content or not str(content).strip():
            logger.warning(
                f"LLM returned empty response for {llm.model_name if hasattr(llm, 'model_name') else 'unknown'}. "
                "Retrying..."
            )
            raise RuntimeError(
                "LLM returned an empty response (possible safety filter or "
                "token budget issue)"
            )
        return resp

    @retry(
        wait=wait_exponential(min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_transient_error),
    )
    async def _fetch_url(self, url: str) -> dict | None:
        try:
            return await asyncio.to_thread(self.stockbit_client.get, url)
        except Exception as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            raise

    # ── Phase 1 — Parallel Data Nodes (all on Flash) ────────────────────────

    async def _fundamental_node(self, state: DebateChamberState) -> dict:
        ticker = state["ticker"]
        current_price = state.get("current_price", 0.0)
        logger.info(f"[Fundamental] Fetching for {ticker}")
        try:
            raw = await self._fetch_url(
                f"{BASE_URL}/keystats/ratio/v1/{ticker}?year_limit=10"
            )
            if not raw:
                return {"fundamental_data": "Data Unavailable"}

            report_str, fv_price = build_fair_value_report(raw, ticker, current_price)
            logger.info(f"[Fundamental] Fair value for {ticker}: {fv_price}")
            if fv_price is None:
                logger.warning(f"[Fundamental] Raw API response for {ticker}: {json.dumps(raw)[:2000]}")

            messages = [
                SystemMessage(content=FUNDAMENTAL_SCOUT_PROMPT),
                HumanMessage(content=f"{report_str}\n\n=== RAW API JSON ===\n{json.dumps(raw)[:10_000]}"),
            ]
            resp = await self._invoke_llm(self.flash_llm, messages)
            return {
                "fundamental_data": resp.content,
                "fair_value_estimate": fv_price,
            }
        except Exception as e:
            logger.error(f"[Fundamental] Error: {e}")
            return {"fundamental_data": "Data Unavailable (Error)"}

    async def _chartist_node(self, state: DebateChamberState) -> dict:
        """Chartist with real OHLCV from yfinance — pre-computes all technicals in Python."""
        ticker = state["ticker"]
        logger.info(f"[Chartist] Fetching OHLCV + orderbook for {ticker}")
        await asyncio.sleep(0.5)  # stagger to avoid burst rate-limit

        # ── 1. Download real price history from yfinance ─────────────────────
        tech_indicators: dict = {}
        try:
            df_yf = await asyncio.to_thread(
                yf.download, f"{ticker}.JK", period="1y", progress=False
            )
            if df_yf is not None and len(df_yf) >= 20:
                # yfinance 1.3.0+ returns MultiIndex columns for single tickers:
                # ('Close', 'ADRO.JK') — flatten to plain column names
                if isinstance(df_yf.columns, pd.MultiIndex):
                    df_yf.columns = df_yf.columns.get_level_values(0)

                close = df_yf['Close'].squeeze()
                high = df_yf['High'].squeeze()
                low = df_yf['Low'].squeeze()
                volume = df_yf['Volume'].squeeze()

                # Pre-compute all technicals in Python (ground truth)
                sma20_val = float(close.rolling(20).mean().iloc[-1])
                ma50_raw = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
                ma200_raw = close.rolling(200).mean().iloc[-1] if len(close) >= 200 else None
                rsi_val = float(compute_rsi(close).iloc[-1])
                atr_val = float(compute_atr(high, low, close).iloc[-1])

                tech_indicators = {
                    "current_price": round(float(close.iloc[-1]), 0),
                    "sma20": round(sma20_val, 0),
                    "ma50": round(float(ma50_raw), 0) if ma50_raw is not None and not pd.isna(ma50_raw) else None,
                    "ma200": round(float(ma200_raw), 0) if ma200_raw is not None and not pd.isna(ma200_raw) else None,
                    "rsi14": round(rsi_val, 1),
                    "atr14": round(atr_val, 0),
                    "avg_volume_20d": round(float(volume.tail(20).mean()), 0),
                    "52w_high": round(float(close.max()), 0),
                    "52w_low": round(float(close.min()), 0),
                }
                logger.info(f"[Chartist] Technicals computed: MA50={tech_indicators.get('ma50')}, RSI={tech_indicators.get('rsi14')}")
        except Exception as e:
            logger.warning(f"[Chartist] yfinance download failed for {ticker}: {e}")

        # ── 2. Also fetch orderbook for near-term level context ──────────────
        orderbook_data: dict = {}
        try:
            orderbook_data = await self._fetch_url(
                f"{BASE_URL}/company-price-feed/v2/orderbook/companies/{ticker}"
            ) or {}
        except Exception as e:
            logger.warning(f"[Chartist] Orderbook fetch failed: {e}")

        # ── 3. Build message with ground-truth technicals ────────────────────
        tech_summary = json.dumps(tech_indicators, indent=2) if tech_indicators else "{}"
        messages = [
            SystemMessage(content=CHARTIST_PROMPT),
            HumanMessage(content=(
                f"=== PRE-COMPUTED TECHNICALS (Python — Ground Truth, do NOT recalculate) ===\n"
                f"{tech_summary}\n\n"
                f"=== ORDERBOOK ===\n{json.dumps(orderbook_data)[:5_000]}"
            )),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)
        return {
            "technical_data": resp.content,
            "technical_indicators": tech_indicators,
        }

    async def _sentiment_node(self, state: DebateChamberState) -> dict:
        ticker = state["ticker"]
        logger.info(f"[Sentiment] Fetching for {ticker}")
        await asyncio.sleep(1.0)   # stagger to avoid burst rate-limit
        try:
            raw = await self._fetch_url(
                f"{BASE_URL}/stream/v3/symbol/{ticker}/pinned"
            )
            if not raw:
                return {"sentiment_data": "Data Unavailable"}
            messages = [
                SystemMessage(content=SENTIMENT_PROMPT),
                HumanMessage(content=json.dumps(raw)[:10_000]),
            ]
            resp = await self._invoke_llm(self.flash_llm, messages)
            return {"sentiment_data": resp.content}
        except Exception as e:
            logger.error(f"[Sentiment] Error: {e}")
            return {"sentiment_data": "Data Unavailable (Error)"}

    async def _synthesizer_node(self, state: DebateChamberState) -> dict:
        """
        Fan-in: merge the three parallel data briefs into one context string.
        Also runs the Margin-of-Safety pre-check and injects any warnings
        so that debate agents are immediately aware of overvaluation risk.
        """
        logger.info("[Synthesizer] Merging parallel data + margin-of-safety check")
        from utils.exdate_scanner import scan_exdate, format_exdate_block

        ticker = state["ticker"]
        f = state.get("fundamental_data", "Missing")
        t = state.get("technical_data", "Missing")
        s = state.get("sentiment_data", "Missing")
        current_price = state.get("current_price", 0.0)
        tech = state.get("technical_indicators", {})

        # Fetch ex-date info (non-blocking — returns CLEAR on failure)
        exdate_info = await asyncio.to_thread(
            scan_exdate, ticker, current_price
        )
        exdate_block = format_exdate_block(ticker, exdate_info)

        # Include pre-computed technical indicators in the synthesized data
        tech_block = ""
        if tech:
            tech_block = (
                f"\n=== PRE-COMPUTED TECHNICAL INDICATORS (Python Ground Truth) ===\n"
                f"{json.dumps(tech, indent=2)}\n"
            )

        raw = (
            f"=== FUNDAMENTALS ===\n{f}\n\n"
            f"=== TECHNICALS ===\n{t}\n"
            f"{tech_block}\n"
            f"=== SENTIMENT ===\n{s}\n\n"
            f"{exdate_block}"
        )

        # ── Margin-of-Safety pre-check (pure Python, zero token cost) ──────
        fair_value_estimate = state.get("fair_value_estimate") or 0.0
        current_price = state.get("current_price") or 0.0

        if fair_value_estimate > 0 and current_price > 0:
            validation = validate_swing_targets(
                current_price=current_price,
                fair_value=fair_value_estimate,
                target_price=0.0,     # not known yet — only overvaluation checked here
                entry_price_range="0 - 0",
                stop_loss=0.0,
            )
            if not validation["is_valid"]:
                raw = (
                    f"[🚨 MARGIN-OF-SAFETY ALERT — Read Before Debating]\n"
                    f"{validation['warning_text']}\n"
                    f"Current Price: Rp {current_price:,.0f} | "
                    f"Estimated Fair Value: Rp {fair_value_estimate:,.0f}\n"
                    f"{'─' * 60}\n\n" + raw
                )
                logger.warning(f"[Synthesizer] Overvaluation detected: {current_price} > {fair_value_estimate}")

        if "Unavailable" in raw or "Missing" in raw:
            raw = (
                "[⚠️ WARNING: One or more data sources failed. "
                "Analysts must caveat conclusions accordingly.]\n\n" + raw
            )

        return {
            "raw_data": raw,
            "fair_value_estimate": fair_value_estimate,
        }

    # ── Phase 2 — Debate Nodes (on Pro) ─────────────────────────────────────

    async def _bullish_node(self, state: DebateChamberState) -> dict:
        ticker = state["ticker"]
        rc = state["round_count"]
        logger.info(f"[Bull] Round {rc + 1} for {ticker}")

        prompt = BULL_SYSTEM_PROMPT_R1 if rc == 0 else BULL_SYSTEM_PROMPT_R2

        content_parts = [f"Ticker: {ticker}\n\nSynthesized Market Data:\n{state['raw_data']}"]

        if rc > 0:
            # Send pruned history — prevents state bloat
            hist = "\n".join(
                f"[{m.role.upper()} R{m.round_num}]: {m.content}"
                for m in state["debate_history"]
            )
            content_parts.append(f"\n\nDebate History (may be pruned summary):\n{hist}")

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content="\n".join(content_parts)),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)
        content = str(resp.content).strip()
        if len(content) < 50:
            logger.warning(
                f"[Bull] Suspiciously short response for {ticker} R{rc+1} "
                f"({len(content)} chars) — may indicate a safety filter hit"
            )
        msg = DebateMessage(role="bull", content=content, round_num=rc + 1)
        return {"debate_history": [msg]}

    async def _bearish_node(self, state: DebateChamberState) -> dict:
        ticker = state["ticker"]
        rc = state["round_count"]
        logger.info(f"[Bear] Round {rc + 1} for {ticker}")

        prompt = BEAR_SYSTEM_PROMPT_R1 if rc == 0 else BEAR_SYSTEM_PROMPT_R2

        # Always surface the latest Bull argument for the Bear to attack
        bull_args = [m.content for m in state["debate_history"] if m.role == "bull"]
        last_bull = bull_args[-1] if bull_args else "(no bull argument yet)"

        content_parts = [
            f"Ticker: {ticker}\n\nSynthesized Market Data:\n{state['raw_data']}",
            f"\n\nBull's argument to challenge:\n{last_bull}",
        ]

        if rc > 0:
            bear_args = [m.content for m in state["debate_history"] if m.role == "bear"]
            if bear_args:
                content_parts.append(
                    f"\n\nYour own Round 1 argument (DO NOT repeat this):\n{bear_args[-1]}"
                )

        messages = [
            SystemMessage(content=prompt),
            HumanMessage(content="\n".join(content_parts)),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)  # Temporarily using flash_llm
        new_rc = rc + 1
        content = str(resp.content).strip()
        if len(content) < 50:
            logger.warning(
                f"[Bear] Suspiciously short response for {ticker} R{new_rc} "
                f"({len(content)} chars) — may indicate a safety filter hit"
            )
        msg = DebateMessage(role="bear", content=content, round_num=new_rc)
        return {"debate_history": [msg], "round_count": new_rc}

    # ── Phase 3 — Adaptive Logic ─────────────────────────────────────────────

    async def _consensus_evaluator_node(self, state: DebateChamberState) -> dict:
        """
        Short-circuit check: if Bull & Bear essentially agree after Round 1,
        skip Round 2 and proceed directly to Devil's Advocate → CIO.
        Uses Flash (cheap) — no Pro tokens wasted here.
        """
        logger.info("[Consensus] Evaluating agreement")
        # Only inspect the two most recent messages (latest round)
        recent = [
            m for m in state["debate_history"]
            if m.round_num == state["round_count"]
        ]
        hist = "\n".join(f"[{m.role.upper()}]: {m.content}" for m in recent)

        messages = [
            SystemMessage(content=CONSENSUS_PROMPT),
            HumanMessage(content=hist),
        ]
        
        try:
            # FIX: Use regular LLM instead of structured for consensus
            resp = await self._invoke_llm(self.flash_llm, messages, inject_rules=False)
            content = str(resp.content).strip().lower()
            agreed = "true" in content or "yes" in content
        except Exception as e:
            logger.warning(f"[Consensus] Failed ({e}); defaulting to False")
            agreed = False

        logger.info(f"[Consensus] Result: {agreed}")
        return {"consensus_reached": agreed}

    #: Regex matching IHSG price mentions in LLM output.  Handles Indonesian
    #: formatting (dot as thousand separator) and the occasional "Rp." with
    #: a period.  Requires ≥3 digits/punctuation to avoid picking up trivial
    #: "Rp 5" noise from prompt instructions.
    _PRICE_RE = re.compile(r"Rp\.?\s*([\d][\d,\.]{2,})", re.IGNORECASE)

    async def _state_cleaner_node(self, state: DebateChamberState) -> dict:
        """
        Deterministic context pruner — zero-LLM, zero-hallucination.

        Rather than asking a model to compress the debate (which often drops
        the exact numbers we care about), we:

        1. Truncate each message to its last ``TAIL_CHARS`` characters so
           conclusions — the bit the next round cares about — are preserved.
        2. Extract every Rp-denominated price mention across the full history
           via regex and emit a dedicated "PRICES CITED" evidence line.
        3. Return the compacted history via the ``round_num=-1`` sentinel
           used by ``history_updater`` to replace (not append) the state.

        This saves one Flash call per Round-1 debate and guarantees the
        Round-2 agents see every price that was debated.
        """

        logger.info("[State Cleaner] Deterministic pruning (no LLM)")
        TAIL_CHARS = 600

        preserved_prices: list[str] = []
        seen: set[str] = set()
        compressed_msgs: list[DebateMessage] = []

        for m in state["debate_history"]:
            # Capture every distinct price mentioned in this message
            for match in self._PRICE_RE.findall(m.content):
                normalised = match.strip().rstrip(".,")
                if normalised and normalised not in seen:
                    seen.add(normalised)
                    preserved_prices.append(normalised)

            # Tail-truncate — conclusions tend to live at the end of the
            # message, which is exactly what the next round needs.
            content = m.content
            truncated = content if len(content) <= TAIL_CHARS else "…" + content[-TAIL_CHARS:]
            compressed_msgs.append(
                DebateMessage(role=m.role, content=truncated, round_num=m.round_num)
            )

        evidence_content = (
            "PRICES CITED IN ROUND 1 (hard evidence — do NOT forget):\n"
            + ", ".join(f"Rp {p}" for p in preserved_prices[:25])
            if preserved_prices
            else "PRICES CITED IN ROUND 1: (none detected)"
        )
        evidence_msg = DebateMessage(role="system", content=evidence_content, round_num=0)

        sentinel = DebateMessage(role="system", content="__REPLACE__", round_num=-1)
        return {"debate_history": [sentinel, evidence_msg, *compressed_msgs]}

    async def _devils_advocate_node(self, state: DebateChamberState) -> dict:
        """
        Injects a worst-case macro challenge before the CIO decides.
        Keeps the CIO from rubber-stamping the winning side.
        """
        logger.info("[Devil's Advocate] Injecting adversarial scenario")
        hist = "\n".join(
            f"[{m.role.upper()} R{m.round_num}]: {m.content}"
            for m in state["debate_history"]
        )
        messages = [
            SystemMessage(content=DEVILS_ADVOCATE_PROMPT),
            HumanMessage(content=f"Data:\n{state['raw_data']}\n\nDebate:\n{hist}"),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)
        msg = DebateMessage(
            role="devils_advocate",
            content=resp.content,
            round_num=state["round_count"] + 1,
        )
        return {
            "debate_history": [msg],
            "devils_advocate_question": resp.content,
        }

    # ── Signal Classifier (pure Python — deterministic) ─────────────────────

    #: Fundamental tolerance — price ≤ fair_value × (1 + FV_TOL) counts as ✅.
    #: 5% slack prevents a stock that is *barely* above intrinsic value from
    #: getting a hard AVOID signal.
    FV_TOL = 0.05

    #: Technical tolerance band around MA50:
    #:   - MA50 × (1 − MA_LOW_TOL)  ≤ price ≤ MA50 × MA_HIGH_TOL    → ✅
    #:   - MA50 × MA_HIGH_TOL       < price ≤ MA50 × MA_OVEREXT     → ✅ but flagged
    #:   - price > MA50 × MA_OVEREXT                                → ❌ (too extended)
    MA_LOW_TOL = 0.02        # 2% below MA50 still counts as support test
    MA_HIGH_TOL = 1.08       # 8% above MA50 is the "overextended soft boundary"
    MA_OVEREXT = 1.10        # 10% above MA50 is a hard reject

    def _classify_signals(
        self,
        current_price: float,
        fair_value: float,
        ma50: float,
    ) -> tuple[bool | None, bool | None, bool, str]:
        """
        Classify the trade setup using tolerance bands (not binary thresholds).

        Returns:
            (fundamental_ok, technical_ok, overextended_flag, reason_str)

            - fundamental_ok: True/False/None.  ``None`` means we could not
              compute (missing fair value) — treated by callers as ❌ but the
              rationale distinguishes "missing" from "overvalued".
            - technical_ok: True/False/None (same semantics).
            - overextended_flag: True if price is 8–10% above MA50.  The
              classification still counts as ✅ in this band, but callers
              should reduce confidence to reflect the poor swing entry timing.
            - reason_str: human-readable explanation for weighted_reasoning.
        """

        # ── Fundamental ─────────────────────────────────────────────────────
        if fair_value is None or fair_value <= 0:
            fundamental_ok: bool | None = None
            fund_reason = "fair_value=null (insufficient fundamental data)"
        else:
            fv_ceiling = fair_value * (1 + self.FV_TOL)
            fundamental_ok = current_price <= fv_ceiling
            fund_reason = (
                f"price Rp {current_price:,.0f} vs FV ceiling Rp {fv_ceiling:,.0f} "
                f"(FV Rp {fair_value:,.0f} + {self.FV_TOL:.0%} tolerance) → "
                f"{'within tolerance' if fundamental_ok else 'overvalued'}"
            )

        # ── Technical ───────────────────────────────────────────────────────
        overextended_flag = False
        if ma50 is None or ma50 <= 0:
            technical_ok: bool | None = None
            tech_reason = "ma50 unavailable"
        else:
            ma_floor = ma50 * (1 - self.MA_LOW_TOL)
            ma_soft_ceiling = ma50 * self.MA_HIGH_TOL
            ma_hard_ceiling = ma50 * self.MA_OVEREXT

            if current_price > ma_hard_ceiling:
                technical_ok = False
                tech_reason = (
                    f"EXTENDED: price Rp {current_price:,.0f} > MA50×{self.MA_OVEREXT:.2f} "
                    f"(Rp {ma_hard_ceiling:,.0f}) — swing entry window missed"
                )
            elif current_price > ma_soft_ceiling:
                technical_ok = True
                overextended_flag = True
                tech_reason = (
                    f"price Rp {current_price:,.0f} is 8–10% above MA50 Rp {ma50:,.0f} "
                    f"(overextended soft zone)"
                )
            elif current_price >= ma_floor:
                technical_ok = True
                tech_reason = (
                    f"price Rp {current_price:,.0f} within MA50 band "
                    f"[Rp {ma_floor:,.0f}, Rp {ma_soft_ceiling:,.0f}]"
                )
            else:
                technical_ok = False
                tech_reason = (
                    f"price Rp {current_price:,.0f} below MA50 floor Rp {ma_floor:,.0f} — "
                    f"downtrend"
                )

        reason = f"{fund_reason}; {tech_reason}"
        return fundamental_ok, technical_ok, overextended_flag, reason

    # ── Trade Envelope Helpers (pure Python — deterministic) ─────────────────

    def _compute_trade_envelope(
        self,
        current_price: float,
        fair_value: float,
        tech: dict,
    ) -> dict:
        """Compute entry/target/stop in Python. All prices snapped to IHSG tick sizes."""
        sma20 = tech.get("sma20", current_price)
        ma50 = tech.get("ma50")
        atr14 = tech.get("atr14", 0)

        # Entry zone: near MA50 support (pullback entry for swing)
        if ma50 and ma50 > 0 and current_price > 0:
            entry_low = snap_to_tick(min(ma50, current_price * 0.97))
            entry_high = snap_to_tick(min(ma50 * 1.02, current_price))
        else:
            entry_low = snap_to_tick(current_price * 0.97)
            entry_high = snap_to_tick(current_price)

        # Ensure entry_low < entry_high
        if entry_low >= entry_high:
            entry_low = snap_to_tick(current_price * 0.96)
            entry_high = snap_to_tick(current_price)
        if entry_low >= entry_high:
            entry_high = entry_low + max(snap_to_tick(entry_low * 0.02), 10)

        entry_mid = (entry_low + entry_high) / 2

        # Stop loss with buffer and hard floor
        if atr14 > 0 and sma20 > 0:
            stop_candidate_1 = sma20 - atr14
            stop_candidate_2 = current_price - (2.0 * atr14)
            stop = max(stop_candidate_1, stop_candidate_2)
            
            # Hard floor: stop tidak boleh lebih dari 8% dari current price
            hard_floor = current_price * 0.92
            stop = snap_to_tick(max(stop, hard_floor))
        else:
            stop = snap_to_tick(entry_mid * 0.96)

        # Guarantee stop < entry_low dengan margin minimal 1 tick
        if stop >= entry_low:
            stop = snap_to_tick(entry_low * 0.96)
        if stop >= entry_low:  # double-check post snap
            stop = entry_low - snap_to_tick(entry_low * 0.01)
            stop = max(stop, entry_mid * 0.90)  # absolute safety net

        # Target calculation (ATR-based with floor and ceiling)
        risk_per_share = entry_mid - stop
        rr_target = entry_mid + (risk_per_share * 2.0)
        
        # Floor: minimal 4% from entry for worthwhile swing
        min_target = entry_mid * 1.04
        target = max(rr_target, min_target)
        target = snap_to_tick(target)
        
        # Ceiling: blend with Fair Value if target > FV
        if fair_value > 0 and target > fair_value:
            target = snap_to_tick((target + fair_value) / 2)

        # Compute R/R ratio
        gain_pct = ((target - entry_mid) / entry_mid) * 100 if entry_mid > 0 else 0
        loss_pct = ((entry_mid - stop) / entry_mid) * 100 if entry_mid > 0 and entry_mid > stop else 0
        rr_ratio = round(gain_pct / loss_pct, 2) if loss_pct > 0 else 0.0

        return {
            "entry_low": entry_low,
            "entry_high": entry_high,
            "entry_mid": round(entry_mid, 0),
            "target_price": target,
            "stop_loss": stop,
            "expected_return_pct": round(gain_pct, 1),
            "max_risk_pct": round(loss_pct, 1),
            "risk_reward_ratio": rr_ratio,
            "fair_value": fair_value if fair_value > 0 else None,
            "atr14": atr14,
        }

    def _format_trade_envelope(self, envelope: dict) -> str:
        """Format trade envelope as a human-readable string for the CIO prompt."""
        fv = envelope.get("fair_value")
        fv_str = f"Rp {fv:,.0f}" if fv else "N/A (insufficient data)"
        return (
            f"FAIR VALUE         : {fv_str}\n"
            f"ENTRY ZONE         : Rp {envelope['entry_low']:,.0f} – Rp {envelope['entry_high']:,.0f}\n"
            f"ENTRY MIDPOINT     : Rp {envelope['entry_mid']:,.0f}\n"
            f"TARGET PRICE       : Rp {envelope['target_price']:,.0f}\n"
            f"STOP LOSS          : Rp {envelope['stop_loss']:,.0f}\n"
            f"ATR(14)            : Rp {envelope['atr14']:,.0f}\n"
            f"EXPECTED RETURN    : +{envelope['expected_return_pct']:.1f}%\n"
            f"MAX RISK           : -{envelope['max_risk_pct']:.1f}%\n"
            f"RISK/REWARD RATIO  : {envelope['risk_reward_ratio']:.2f}\n"
            f"\n"
            f"⚠️ These prices are IHSG tick-rounded and Python-computed.\n"
            f"   CIO must use these VERBATIM — do NOT override."
        )

    # ── Phase 4 — CIO Judge ──────────────────────────────────────────────────

    async def _cio_judge_node(self, state: DebateChamberState) -> dict:
        """
        Weighted synthesis verdict — Swing Trade edition.
        Outputs a Pydantic-validated CIOVerdict with concrete price levels.

        Key change: entry/target/stop are computed in Python (trade envelope)
        and the LLM is instructed to use them verbatim. After LLM returns,
        Python overrides any LLM-generated prices with the envelope values.
        """
        ticker = state["ticker"]
        current_price = state.get("current_price", 0.0)
        tech = state.get("technical_indicators", {})
        fair_value = state.get("fair_value_estimate", 0.0)
        logger.info(f"[CIO] Deliberating on {ticker} (current price: {current_price:,.0f})")

        # ── Compute Trade Envelope (deterministic, Python-only) ──────────────
        envelope = self._compute_trade_envelope(current_price, fair_value, tech)
        envelope_text = self._format_trade_envelope(envelope)

        # ── Conflict Resolution signal (deterministic, Python-only) ──────────
        ma50 = tech.get("ma50", 0) or 0
        fundamental_ok, technical_ok, overextended_flag, signal_reason = (
            self._classify_signals(current_price, fair_value, ma50)
        )

        if fundamental_ok and technical_ok:
            conflict_signal = (
                "SIGNAL: Fundamental ✅ + Technical ✅ → Lean BUY (confidence ≥ 0.70). "
                f"Rationale: {signal_reason}."
            )
        elif fundamental_ok and not technical_ok:
            conflict_signal = (
                "SIGNAL: Fundamental ✅ + Technical ❌ → Lean HOLD (Wait for technical confirmation). "
                f"Rationale: {signal_reason}."
            )
        elif (fundamental_ok is False) and technical_ok:
            conflict_signal = (
                "SIGNAL: Fundamental ❌ + Technical ✅ → If Foreign Flow / Sentiment is strongly "
                "positive and Volume supports, Lean BUY (Momentum Play). Otherwise, HOLD. "
                f"Rationale: {signal_reason}."
            )
        else:
            conflict_signal = (
                "SIGNAL: Fundamental ❌ + Technical ❌ → Lean AVOID. "
                f"Rationale: {signal_reason}."
            )

        if overextended_flag:
            conflict_signal += (
                "\n⚠️ OVEREXTENDED FLAG: Price is 8–10% above MA50 — swing entry is "
                "risky; confidence should be reduced by at least 0.10 even if other signals agree."
            )

        # ── Build CIO prompt ─────────────────────────────────────────────────
        hist = "\n".join(
            f"[{m.role.upper()} R{m.round_num}]: {m.content}"
            for m in state["debate_history"]
        )
        user_content = (
            f"Ticker: {ticker}\n"
            f"Current Market Price: Rp {current_price:,.0f}\n\n"
            f"=== TRADE ENVELOPE (Python-Computed — Use VERBATIM) ===\n"
            f"{envelope_text}\n\n"
            f"=== CONFLICT RESOLUTION ===\n"
            f"{conflict_signal}\n\n"
            f"Synthesized Market Data:\n{state['raw_data']}\n\n"
            f"Full Debate Transcript:\n{hist}\n\n"
            f"Devil's Advocate Challenge:\n{state.get('devils_advocate_question', 'N/A')}"
        )

        # ── JSON schema injected into the prompt so we bypass LangChain's
        #    with_structured_output() parser entirely.  That parser wraps the
        #    Gemini call and raises OUTPUT_PARSING_FAILURE whenever the model
        #    returns markdown fences or any extra text around the JSON — which
        #    Gemini does ~90% of the time.  Calling pro_llm directly and
        #    cleaning the response ourselves is far more reliable.
        json_schema_hint = """\

=== REQUIRED OUTPUT FORMAT ===
Respond with ONLY a single valid JSON object. No markdown fences, no preamble,
no trailing text. The JSON must have exactly these keys:

{
  "ticker": "<string>",
  "rating": "<STRONG_BUY | BUY | HOLD | AVOID>",
  "confidence": <float 0.0-1.0>,
  "summary": "<string — 2-4 sentence CIO verdict>",
  "weighted_reasoning": "<string — explain how signals were weighted>",
  "key_catalysts": ["<string>", ...],
  "key_risks": ["<string>", ...],
  "timeframe": "<string e.g. '1-3 Months'>",
  "entry_price_range": "<string e.g. '4800 - 5000'>",
  "target_price": <number>,
  "stop_loss": <number>,
  "current_price": <number>,
  "fair_value": <number or null>,
  "expected_return": "<string e.g. '+6.2%'>",
  "risk_reward_ratio": <float>
}

Start your response with '{' and end with '}'. Nothing else."""

        messages = [
            SystemMessage(content=CIO_SYSTEM_PROMPT + json_schema_hint),
            HumanMessage(content=user_content),
        ]

        def _sanitize_json(text: str) -> str:
            """
            Clean common LLM JSON mistakes before json.loads().

            Handles (in order):
              1. Markdown code fences  — ```json ... ```
              2. Preamble before {     — "Here is the JSON: {...}"
              3. Trailing text after } — "{...} Hope this helps!"
              4. Trailing commas       — {"a": 1,} or ["x",]
              5. Python-style comments — // ... or # ... on their own line
              6. Single-quoted keys    — {'key': 'val'}
              7. Unescaped quotes inside strings (must preserve structure)
              8. Newlines inside string values (replace with space)
            """
            # 1. Strip markdown fences
            text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
            text = re.sub(r"\n?```\s*$", "", text).strip()
            # 2. Find first { and last } to trim preamble/postamble
            brace = text.find("{")
            if brace > 0:
                text = text[brace:]
            rbrace = text.rfind("}")
            if rbrace != -1 and rbrace < len(text) - 1:
                text = text[: rbrace + 1]
            # 3. Remove JS/Python-style comments (// ... and # ... lines)
            text = re.sub(r"//[^\n]*", "", text)
            text = re.sub(r"#[^\n]*", "", text)
            # 4. Fix trailing commas before ] or }  e.g. [1, 2,] or {"a":1,}
            text = re.sub(r",\s*([\]}])", r"\1", text)
            # 5. Replace newlines inside double-quoted strings with space
            #    This handles multi-line summary/reasoning fields
            result = []
            in_string = False
            escape_next = False
            for char in text:
                if escape_next:
                    result.append(char)
                    escape_next = False
                elif char == "\\":
                    result.append(char)
                    escape_next = True
                elif char == '"':
                    in_string = not in_string
                    result.append(char)
                elif in_string and char in ("\n", "\r", "\t"):
                    result.append(" ")  # Replace whitespace inside strings
                else:
                    result.append(char)
            text = "".join(result).strip()
            
            # 6. Final safety: if still can't parse, convert single quotes outside strings to double
            try:
                json.loads(text)
                return text
            except json.JSONDecodeError:
                # Last resort: fix 'key' to "key" pattern outside of strings
                final = []
                in_str = False
                esc = False
                for ch in text:
                    if esc:
                        final.append(ch)
                        esc = False
                    elif ch == "\\" and in_str:
                        final.append(ch)
                        esc = True
                    elif ch == '"':
                        in_str = not in_str
                        final.append(ch)
                    elif ch == "'" and not in_str:
                        final.append('"')
                    else:
                        final.append(ch)
                return "".join(final).strip()
        def _apply_envelope(parsed: dict) -> dict:
            """
            Overwrite LLM-supplied price fields with Python-computed envelope values.
            Ensures numeric types and a canonical 'entry_low - entry_high' range string.
            """
            p = dict(parsed) if isinstance(parsed, dict) else {}
            p.setdefault("ticker", ticker)
            try:
                p["current_price"] = float(p.get("current_price") or current_price or 0.0)
            except Exception:
                p["current_price"] = float(current_price or 0.0)
            try:
                entry_low = int(envelope.get("entry_low") or envelope.get("entry_mid") or 0)
                entry_high = int(envelope.get("entry_high") or envelope.get("entry_mid") or 0)
                p["entry_price_range"] = f"{entry_low} - {entry_high}"
            except Exception:
                p["entry_price_range"] = p.get("entry_price_range") or ""
            try:
                p["target_price"] = int(envelope.get("target_price")) if envelope.get("target_price") is not None else p.get("target_price")
            except Exception:
                p["target_price"] = p.get("target_price")
            try:
                p["stop_loss"] = int(envelope.get("stop_loss")) if envelope.get("stop_loss") is not None else p.get("stop_loss")
            except Exception:
                p["stop_loss"] = p.get("stop_loss")
            fv_env = envelope.get("fair_value")
            if fv_env is not None and fv_env != 0:
                try:
                    p["fair_value"] = int(fv_env)
                except Exception:
                    p["fair_value"] = fv_env
            else:
                p["fair_value"] = p.get("fair_value") or None
            return p

        try:
            resp = await self._invoke_llm(self.pro_llm, messages, inject_rules=False)
            parsed = json.loads(_sanitize_json(resp.content))
            parsed = _apply_envelope(parsed)
            verdict_json = CIOVerdict(**parsed).model_dump_json()
            logger.info(f"[CIO] JSON parsed successfully for {ticker}")
        except Exception as e:
            logger.warning(f"[CIO] Primary JSON parse failed ({e}); using safe fallback verdict")
            verdict_json = CIOVerdict(
                ticker=ticker,
                rating="HOLD",
                confidence=0.0,
                summary=f"CIO parse error — raw response stored. Error: {e}",
                current_price=current_price,
                fair_value=envelope["fair_value"],
                entry_price_range=f"{int(envelope['entry_low'])} - {int(envelope['entry_high'])}",
                target_price=envelope["target_price"],
                stop_loss=envelope["stop_loss"],
            ).model_dump_json()

        logger.info(f"[CIO] Verdict delivered for {ticker}")
        return {"final_verdict": verdict_json}

    # ── Graph Assembly ───────────────────────────────────────────────────────

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(DebateChamberState)

        # Register nodes
        graph.add_node("fundamental",         self._fundamental_node)
        graph.add_node("chartist",            self._chartist_node)
        graph.add_node("sentiment",           self._sentiment_node)
        graph.add_node("synthesizer",         self._synthesizer_node)
        graph.add_node("bullish_analyst",     self._bullish_node)
        graph.add_node("bearish_auditor",     self._bearish_node)
        graph.add_node("consensus_evaluator", self._consensus_evaluator_node)
        graph.add_node("state_cleaner",       self._state_cleaner_node)
        graph.add_node("devils_advocate",     self._devils_advocate_node)
        graph.add_node("cio_judge",           self._cio_judge_node)

        # Phase 1: Parallel fan-out from START
        graph.add_edge(START, "fundamental")
        graph.add_edge(START, "chartist")
        graph.add_edge(START, "sentiment")

        # Phase 1: Fan-in to synthesizer
        graph.add_edge("fundamental", "synthesizer")
        graph.add_edge("chartist",    "synthesizer")
        graph.add_edge("sentiment",   "synthesizer")

        # Phase 2: Debate cycle
        graph.add_edge("synthesizer",     "bullish_analyst")
        graph.add_edge("bullish_analyst", "bearish_auditor")
        graph.add_edge("bearish_auditor", "consensus_evaluator")

        # Phase 3: Adaptive routing
        graph.add_conditional_edges("consensus_evaluator", post_evaluator_router)
        graph.add_edge("state_cleaner", "bullish_analyst")   # loops back for R2

        # Phase 4: Conclusion path
        graph.add_edge("devils_advocate", "cio_judge")
        graph.add_edge("cio_judge",       END)

        return graph.compile()

    # ── Public API ───────────────────────────────────────────────────────────

    async def run(self, ticker: str, current_price: float = 0.0) -> dict:
        """
        Execute the full swing-trade debate pipeline for a given IHSG ticker.

        Args:
            ticker        : IHSG stock code, e.g. "BBRI"
            current_price : Last traded price in IDR (e.g. 4875.0).
                            Used by the Synthesizer for margin-of-safety checks
                            and by the CIO for is_overvalued auto-flagging.
                            Pass 0.0 to skip price-level validation.

        Returns:
            The final LangGraph state dict.
            Access the verdict via: json.loads(result["final_verdict"])
            For the Svelte trade card: CIOVerdict(**json.loads(...)).to_trade_card()
        """
        initial_state: DebateChamberState = {
            "ticker": ticker,
            "current_price": current_price,
            "fundamental_data": "",
            "technical_data": "",
            "sentiment_data": "",
            "raw_data": "",
            "technical_indicators": {},
            "fair_value_estimate": 0.0,
            "debate_history": [],
            "round_count": 0,
            "consensus_reached": False,
            "devils_advocate_question": "",
            "final_verdict": "",
            "error": None,
        }
        logger.info(f"[DebateChamber] ▶ Starting swing-trade pipeline for {ticker} @ Rp {current_price:,.0f}")
        result = await self.app.ainvoke(initial_state)
        logger.info(f"[DebateChamber] ✅ Pipeline complete for {ticker}")
        return result