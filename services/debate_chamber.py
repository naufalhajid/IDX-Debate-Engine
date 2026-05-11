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
from collections import Counter
import json
import re
from typing import Literal

import pandas as pd
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
from schemas.debate import CIOVerdict, DebateChamberState, DebateMessage, validate_swing_targets
from services.stockbit_api_client import StockbitApiClient
from services.fair_value_calculator import build_fair_value_report
from services.debate_prompt_registry import PROMPT_REGISTRY, PROMPT_VERSION
from utils.logger_config import logger
from utils.market_data_cache import (
    derive_current_price,
    prefetch_market_data,
    scan_exdate_from_market_data,
)
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
    disagreement_type: Literal["direction", "timing", "valuation", "catalyst"] | None = Field(
        default=None,
        description="Primary disagreement type when consensus_reached is false.",
    )


# ---------------------------------------------------------------------------
# System Prompts — 5-agent roster
# ---------------------------------------------------------------------------

# ── Phase 1 Data Scouts (run on Flash — cheap, fast) ────────────────────────

FUNDAMENTAL_SCOUT_PROMPT = PROMPT_REGISTRY.prompts["FUNDAMENTAL_SCOUT_PROMPT"]

CHARTIST_PROMPT = PROMPT_REGISTRY.prompts["CHARTIST_PROMPT"]

SENTIMENT_PROMPT = PROMPT_REGISTRY.prompts["SENTIMENT_PROMPT"]

# ── Phase 2 Debate Agents (Bull/Bear on Flash; Pro reserved for final CIO reasoning) ──────────

BULL_SYSTEM_PROMPT_R1 = PROMPT_REGISTRY.prompts["BULL_SYSTEM_PROMPT_R1"]

BULL_SYSTEM_PROMPT_R2 = PROMPT_REGISTRY.prompts["BULL_SYSTEM_PROMPT_R2"]

BEAR_SYSTEM_PROMPT_R1 = PROMPT_REGISTRY.prompts["BEAR_SYSTEM_PROMPT_R1"]

BEAR_SYSTEM_PROMPT_R2 = PROMPT_REGISTRY.prompts["BEAR_SYSTEM_PROMPT_R2"]

# ── Adaptive nodes ───────────────────────────────────────────────────────────

CONSENSUS_PROMPT = PROMPT_REGISTRY.prompts["CONSENSUS_PROMPT"]

STATE_CLEANER_PROMPT = PROMPT_REGISTRY.prompts["STATE_CLEANER_PROMPT"]

DEVILS_ADVOCATE_PROMPT = PROMPT_REGISTRY.prompts["DEVILS_ADVOCATE_PROMPT"]

# ── CIO Judge — Swing Trade Edition (Phase 4) ───────────────────────────────

CIO_SYSTEM_PROMPT = PROMPT_REGISTRY.prompts["CIO_SYSTEM_PROMPT"]


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
    if (
        state.get("consensus_reached")
        or state.get("consensus_method") == "confidence_winner"
        or state["round_count"] >= MAX_DEBATE_ROUNDS
    ):
        return "devils_advocate"
    return "state_cleaner"


# ---------------------------------------------------------------------------
# DebateChamber
# ---------------------------------------------------------------------------

BASE_URL = "https://exodus.stockbit.com"
CONSENSUS_THRESHOLD = 0.60
ROUND1_CONSENSUS_THRESHOLD = 0.80
CONSENSUS_AGENT_COUNT = 5
MAX_DEBATE_ROUNDS = 3
SOFT_HOLD_CONFIDENCE_DELTA = 0.15

AGENT_SIGNAL_PROMPT = PROMPT_REGISTRY.prompts["AGENT_SIGNAL_PROMPT"]


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
        self.prompt_version = PROMPT_VERSION

    # -- Agent signal helpers -------------------------------------------------

    _CONFIDENCE_RE = re.compile(
        r"(?:agent\s*)?confidence[^0-9]{0,24}"
        r"([01](?:\.\d+)?|[1-9]\d(?:\.\d+)?|100(?:\.0+)?)\s*%?",
        re.IGNORECASE,
    )
    _POSITION_RE = re.compile(
        r"(?:position|rating|verdict|swing_signal)\s*[:=]\s*"
        r"['\"]?(STRONG_BUY|BUY|HOLD|AVOID|SELL|NEUTRAL|BULLISH|BEARISH)",
        re.IGNORECASE,
    )

    @staticmethod
    def _normalise_position(value: str | None) -> str:
        if not value:
            return "UNKNOWN"
        token = value.strip().upper().replace("-", "_").replace(" ", "_")
        if token in {"STRONG_BUY", "BUY", "BULLISH", "ACCUMULATE"}:
            return "BUY"
        if token in {"SELL", "AVOID", "BEARISH", "DISTRIBUTE"}:
            return "AVOID"
        if token in {"HOLD", "NEUTRAL", "WAIT", "WAIT_AND_SEE"}:
            return "HOLD"
        return "UNKNOWN"

    @staticmethod
    def _compact_text(text: str, limit: int = 1_200) -> str:
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(clean) <= limit:
            return clean
        return clean[:limit].rstrip() + "..."

    @classmethod
    def _redact_debate_prices(cls, text: str) -> str:
        return cls._PRICE_RE.sub(
            "Rp [REDACTED: use Python Trade Envelope]",
            str(text or ""),
        )

    @classmethod
    def _extract_confidence(cls, content: str, default: float | None = None) -> float | None:
        text = str(content or "")
        for match in cls._CONFIDENCE_RE.finditer(text):
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            if value > 1.0:
                value = value / 100.0
            return max(0.0, min(value, 1.0))
        return default

    @classmethod
    def _infer_position_from_text(cls, content: str, role: str) -> str:
        text = str(content or "")
        if not text.strip() or "data unavailable" in text.lower() or "missing" == text.lower().strip():
            return "UNKNOWN"

        explicit = cls._POSITION_RE.search(text)
        if explicit:
            return cls._normalise_position(explicit.group(1))

        lowered = text.lower()
        if "insufficient_data" in lowered or "insufficient data" in lowered:
            return "HOLD"

        scores = {
            "BUY": sum(
                token in lowered
                for token in (
                    "strong_buy",
                    " buy",
                    "bullish",
                    "undervalued",
                    "discount",
                    "support holds",
                    "breakout",
                    "viable",
                    "accumulate",
                )
            ),
            "HOLD": sum(
                token in lowered
                for token in (
                    "hold",
                    "wait",
                    "neutral",
                    "sideways",
                    "confirmation",
                    "marginal",
                    "fairly valued",
                )
            ),
            "AVOID": sum(
                token in lowered
                for token in (
                    "avoid",
                    "sell",
                    "bearish",
                    "overvalued",
                    "breakdown",
                    "no margin",
                    "unviable",
                    "high risk",
                    "support breaks",
                )
            ),
        }
        best_position, best_score = max(scores.items(), key=lambda item: item[1])
        if best_score > 0 and list(scores.values()).count(best_score) == 1:
            return best_position

        if role == "bull":
            return "BUY"
        if role == "bear":
            return "AVOID"
        return "UNKNOWN"

    def _extract_agent_signal(self, content: str, role: str) -> dict[str, object]:
        position = self._infer_position_from_text(content, role)
        unavailable = position == "UNKNOWN"
        default_confidence = None if unavailable else (0.60 if role in {"bull", "bear"} else 0.55)
        confidence = self._extract_confidence(content, default=default_confidence)
        return {
            "position": position,
            "confidence": None if confidence is None else round(confidence, 2),
        }

    def _ensure_signal_footer(self, content: str, role: str) -> tuple[str, dict[str, object]]:
        signal = self._extract_agent_signal(content, role)
        confidence = signal.get("confidence")
        if confidence is None:
            confidence = 0.0
            signal["confidence"] = confidence
        if signal.get("position") == "UNKNOWN":
            signal["position"] = "HOLD" if confidence == 0.0 else "UNKNOWN"

        text = str(content or "").strip()
        if "agent confidence" not in text.lower() or "position:" not in text.lower():
            text = (
                f"{text}\n\n"
                f"Position: {signal['position']}\n"
                f"Agent Confidence: {float(confidence):.2f}"
            ).strip()
        return text, signal

    @staticmethod
    def _latest_message(state: DebateChamberState, role: str) -> DebateMessage | None:
        messages = [m for m in state.get("debate_history", []) if m.role == role]
        return messages[-1] if messages else None

    def _collect_agent_votes(self, state: DebateChamberState) -> list[dict[str, object]]:
        specs = [
            ("fundamental_scout", state.get("fundamental_data", ""), "fundamental_scout", 0),
            ("chartist", state.get("technical_data", ""), "chartist", 0),
            ("sentiment_specialist", state.get("sentiment_data", ""), "sentiment_specialist", 0),
        ]
        for role in ("bull", "bear"):
            msg = self._latest_message(state, role)
            if msg is not None:
                specs.append((role, msg.content, role, msg.round_num))
            else:
                specs.append((role, "", role, state.get("round_count", 0)))

        votes: list[dict[str, object]] = []
        for agent, content, role, round_num in specs:
            signal = self._extract_agent_signal(str(content), role)
            confidence = signal.get("confidence")
            votes.append({
                "agent": agent,
                "position": signal.get("position", "UNKNOWN"),
                "confidence": 0.0 if confidence is None else float(confidence),
                "round": round_num,
            })
        return votes

    @staticmethod
    def _dissenters(votes: list[dict[str, object]], consensus_position: str) -> list[str]:
        return [
            str(v["agent"])
            for v in votes
            if v.get("position") not in {consensus_position, "UNKNOWN"}
        ]

    @staticmethod
    def _infer_disagreement_type(votes: list[dict[str, object]]) -> str:
        positions = {str(v.get("position")) for v in votes if v.get("position") != "UNKNOWN"}
        if "BUY" in positions and "AVOID" in positions:
            return "direction"
        if "HOLD" in positions and len(positions) > 1:
            return "timing"
        return "direction"

    def _evaluate_consensus_votes(
        self,
        votes: list[dict[str, object]],
        round_count: int,
    ) -> dict[str, object]:
        bull_vote = next((v for v in votes if v["agent"] == "bull"), None)
        bear_vote = next((v for v in votes if v["agent"] == "bear"), None)

        if bull_vote and bear_vote:
            bull_pos = str(bull_vote.get("position"))
            bear_pos = str(bear_vote.get("position"))
            bull_conf = float(bull_vote.get("confidence", 0.0) or 0.0)
            bear_conf = float(bear_vote.get("confidence", 0.0) or 0.0)
            if (
                bull_pos != "UNKNOWN"
                and bear_pos != "UNKNOWN"
                and bull_pos != bear_pos
                and abs(bull_conf - bear_conf) < SOFT_HOLD_CONFIDENCE_DELTA
            ):
                if round_count >= 2:
                    return {
                        "consensus_reached": True,
                        "consensus_method": "soft_hold",
                        "disagreement_type": "timing",
                        "dissenting_agents": self._dissenters(votes, "HOLD"),
                        "consensus_winner": {
                            "agent": "soft_hold_rule",
                            "position": "HOLD",
                            "confidence": round(max(bull_conf, bear_conf), 2),
                        },
                        "agent_votes": votes,
                    }

                return {
                    "consensus_reached": False,
                    "consensus_method": None,
                    "disagreement_type": "timing",
                    "dissenting_agents": [],
                    "consensus_winner": None,
                    "agent_votes": votes,
                }

        known_positions = [
            str(v.get("position"))
            for v in votes
            if v.get("position") in {"BUY", "HOLD", "AVOID"}
        ]
        counts = Counter(known_positions)
        if counts:
            position, count = counts.most_common(1)[0]
            threshold = (
                ROUND1_CONSENSUS_THRESHOLD
                if round_count <= 1
                else CONSENSUS_THRESHOLD
            )
            if count / CONSENSUS_AGENT_COUNT >= threshold:
                majority_votes = [v for v in votes if v.get("position") == position]
                winner = max(majority_votes, key=lambda v: float(v.get("confidence", 0.0) or 0.0))
                return {
                    "consensus_reached": True,
                    "consensus_method": "voting",
                    "disagreement_type": None,
                    "dissenting_agents": self._dissenters(votes, position),
                    "consensus_winner": winner,
                    "agent_votes": votes,
                }

        if round_count >= MAX_DEBATE_ROUNDS:
            known_votes = [v for v in votes if v.get("position") in {"BUY", "HOLD", "AVOID"}]
            winner = max(
                known_votes or votes,
                key=lambda v: float(v.get("confidence", 0.0) or 0.0),
            )
            winner_position = str(winner.get("position") or "HOLD")
            if winner_position == "UNKNOWN":
                winner_position = "HOLD"
                winner = {**winner, "position": winner_position}
            return {
                "consensus_reached": False,
                "consensus_method": "confidence_winner",
                "disagreement_type": self._infer_disagreement_type(votes),
                "dissenting_agents": self._dissenters(votes, winner_position),
                "consensus_winner": winner,
                "agent_votes": votes,
            }

        return {
            "consensus_reached": False,
            "consensus_method": None,
            "disagreement_type": self._infer_disagreement_type(votes),
            "dissenting_agents": [],
            "consensus_winner": None,
            "agent_votes": votes,
        }

    # ── LLM & HTTP helpers ──────────────────────────────────────────────────

    def _classify_llm_tier(self, llm) -> str:
        """
        Determine whether this LLM instance is Pro or Flash so we can charge
        the right budget counter.

        Unknown tiers fail fast because budget accounting must be conservative.
        """
        model_name = getattr(llm, "model", None)
        if model_name is None:
            bound = getattr(llm, "bound", None) or getattr(llm, "first", None)
            model_name = getattr(bound, "model", None)
        if model_name is None:
            raise RuntimeError("Unable to classify LLM tier for budget accounting")

        m = str(model_name).lower()
        if "pro" in m:
            return "pro"
        if "flash" in m:
            return "flash"
        raise RuntimeError("Unable to classify LLM tier for budget accounting")

    async def _invoke_llm(self, llm, messages, inject_rules: bool = True):
        """
        Invoke LLM dengan budget guard dan global rules injection.

        Parameter inject_rules dihidupkan/dimatikan untuk memastikan
        structured output (CIO & Consensus) tidak berbenturan instruksi.
        """
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

        tier = self._classify_llm_tier(llm)
        resp = await self._invoke_llm_with_retry(llm, msgs, tier)
        return resp

    @retry(
        wait=wait_exponential(min=2, max=30),
        stop=stop_after_attempt(3),
        retry=retry_if_exception(_is_transient_error),
    )
    async def _invoke_llm_attempt(self, llm, messages):
        try:
            resp = await llm.ainvoke(messages)
        except asyncio.CancelledError:
            raise

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
                "LLM returned an empty response (possible provider issue or "
                "token budget issue)"
            )
        return resp

    async def _invoke_llm_with_retry(self, llm, messages, tier: str):
        if tier == "pro":
            await check_and_increment_pro_budget()
        else:
            await check_and_increment_flash_budget()

        return await self._invoke_llm_attempt(llm, messages)

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

    async def _fetch_market_data(self, ticker: str) -> dict:
        return await prefetch_market_data(ticker)

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
                SystemMessage(content=FUNDAMENTAL_SCOUT_PROMPT + AGENT_SIGNAL_PROMPT),
                HumanMessage(content=f"{report_str}\n\n=== RAW API JSON ===\n{json.dumps(raw)[:10_000]}"),
            ]
            resp = await self._invoke_llm(self.flash_llm, messages)
            content, _signal = self._ensure_signal_footer(resp.content, "fundamental_scout")
            return {
                "fundamental_data": content,
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
            df_yf = (state.get("market_data") or {}).get("history")
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
                ema20_val = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
                ma50_raw = close.rolling(50).mean().iloc[-1] if len(close) >= 50 else None
                ma200_series = close.rolling(window=200, min_periods=50).mean()
                ma200_raw = ma200_series.iloc[-1] if len(close) >= 50 else None
                rsi_val = float(compute_rsi(close).iloc[-1])
                atr_val = float(compute_atr(high, low, close).iloc[-1])
                current_price = float(close.iloc[-1])

                if ma200_raw is None or pd.isna(ma200_raw):
                    ma200_context = "INSUFFICIENT_DATA"
                else:
                    ma200_value = float(ma200_raw)
                    if current_price > ma200_value * 1.02:
                        ma200_context = "ABOVE"
                    elif current_price < ma200_value * 0.98:
                        ma200_context = "BELOW"
                    else:
                        prev5 = close.iloc[-6:-1]
                        if (
                            len(prev5) == 5
                            and float(prev5.mean()) < ma200_value
                            and current_price > ma200_value
                        ):
                            ma200_context = "CROSSOVER_RECENT"
                        else:
                            ma200_context = "ABOVE" if current_price >= ma200_value else "BELOW"

                tech_indicators = {
                    "current_price": round(current_price, 0),
                    "sma20": round(sma20_val, 0),
                    "ema20": round(ema20_val, 0),
                    "ma50": round(float(ma50_raw), 0) if ma50_raw is not None and not pd.isna(ma50_raw) else None,
                    "ma200": round(float(ma200_raw), 0) if ma200_raw is not None and not pd.isna(ma200_raw) else None,
                    "ma200_context": ma200_context,
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
            SystemMessage(content=CHARTIST_PROMPT + AGENT_SIGNAL_PROMPT),
            HumanMessage(content=(
                f"=== PRE-COMPUTED TECHNICALS (Python — Ground Truth, do NOT recalculate) ===\n"
                f"{tech_summary}\n\n"
                f"=== ORDERBOOK ===\n{json.dumps(orderbook_data)[:5_000]}"
            )),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)
        content, _signal = self._ensure_signal_footer(resp.content, "chartist")
        return {
            "technical_data": content,
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
                SystemMessage(content=SENTIMENT_PROMPT + AGENT_SIGNAL_PROMPT),
                HumanMessage(content=json.dumps(raw)[:10_000]),
            ]
            resp = await self._invoke_llm(self.flash_llm, messages)
            content, _signal = self._ensure_signal_footer(resp.content, "sentiment_specialist")
            return {"sentiment_data": content}
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
        from utils.exdate_scanner import format_exdate_block

        ticker = state["ticker"]
        f = state.get("fundamental_data", "Missing")
        t = state.get("technical_data", "Missing")
        s = state.get("sentiment_data", "Missing")
        current_price = state.get("current_price", 0.0)
        tech = state.get("technical_indicators", {})

        # Fetch ex-date info (non-blocking — returns CLEAR on failure)
        exdate_info = scan_exdate_from_market_data(
            ticker,
            state.get("market_data") or {},
            current_price,
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

        decision_brief = (
            f"Ticker: {ticker}\n"
            f"Current Price: Rp {current_price:,.0f}\n"
            f"Fair Value Estimate: "
            f"{f'Rp {fair_value_estimate:,.0f}' if fair_value_estimate else 'INSUFFICIENT_DATA'}\n"
            f"Technical Indicators: {json.dumps(tech, ensure_ascii=False) if tech else '{}'}\n\n"
            f"Fundamental Brief: {self._compact_text(f, 1_000)}\n\n"
            f"Technical Brief: {self._compact_text(t, 1_000)}\n\n"
            f"Sentiment Brief: {self._compact_text(s, 800)}\n\n"
            f"{exdate_block}"
        )

        return {
            "raw_data": raw,
            "decision_brief": decision_brief,
            "fair_value_estimate": fair_value_estimate,
        }

    # ── Phase 2 — Debate Nodes (Bull/Bear on Flash; Pro reserved for CIO) ─────────────────────────────────

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
            SystemMessage(content=prompt + AGENT_SIGNAL_PROMPT),
            HumanMessage(content="\n".join(content_parts)),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)
        content, signal = self._ensure_signal_footer(str(resp.content), "bull")
        if len(content) < 50:
            logger.warning(
                f"[Bull] Suspiciously short response for {ticker} R{rc+1} "
                f"({len(content)} chars) — may indicate a safety filter hit"
            )
        msg = DebateMessage(
            role="bull",
            content=content,
            round_num=rc + 1,
            position=str(signal.get("position", "UNKNOWN")),
            confidence=signal.get("confidence"),
        )
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
            SystemMessage(content=prompt + AGENT_SIGNAL_PROMPT),
            HumanMessage(content="\n".join(content_parts)),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)  # Use Flash for Bear opening/rebuttal rounds
        new_rc = rc + 1
        content, signal = self._ensure_signal_footer(str(resp.content), "bear")
        if len(content) < 50:
            logger.warning(
                f"[Bear] Suspiciously short response for {ticker} R{new_rc} "
                f"({len(content)} chars) — may indicate a safety filter hit"
            )
        msg = DebateMessage(
            role="bear",
            content=content,
            round_num=new_rc,
            position=str(signal.get("position", "UNKNOWN")),
            confidence=signal.get("confidence"),
        )
        return {"debate_history": [msg], "round_count": new_rc}

    # ── Phase 3 — Adaptive Logic ─────────────────────────────────────────────

    async def _consensus_evaluator_node(self, state: DebateChamberState) -> dict:
        """
        Short-circuit check: if Bull & Bear essentially agree after Round 1,
        skip Round 2 and proceed directly to Devil's Advocate → CIO.
        Uses Pro — this reasoning step should not be downgraded to Flash.
        """
        logger.info("[Consensus] Evaluating 5-agent votes")
        votes = self._collect_agent_votes(state)
        result = self._evaluate_consensus_votes(votes, state["round_count"])
        logger.info(
            "[Consensus] Result: "
            f"reached={result['consensus_reached']} "
            f"method={result['consensus_method']} "
            f"winner={result.get('consensus_winner')} "
            f"dissent={result['dissenting_agents']}"
        )
        return result

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
                DebateMessage(
                    role=m.role,
                    content=truncated,
                    round_num=m.round_num,
                    position=m.position,
                    confidence=m.confidence,
                )
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
        decision_context = state.get("decision_brief") or state.get("raw_data", "")
        messages = [
            SystemMessage(content=DEVILS_ADVOCATE_PROMPT),
            HumanMessage(content=f"Decision Brief:\n{decision_context}\n\nDebate:\n{hist}"),
        ]
        resp = await self._invoke_llm(self.flash_llm, messages)
        content, signal = self._ensure_signal_footer(str(resp.content), "devils_advocate")
        msg = DebateMessage(
            role="devils_advocate",
            content=content,
            round_num=state["round_count"] + 1,
            position=str(signal.get("position", "UNKNOWN")),
            confidence=signal.get("confidence"),
        )
        return {
            "debate_history": [msg],
            "devils_advocate_question": content,
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

    @staticmethod
    def _sanitize_json(text: str) -> str:
        """
        Clean common LLM JSON mistakes before json.loads().

        This keeps cleanup conservative: it trims wrapper text/fences and fixes
        trailing commas, but does not remove // or # inside valid JSON strings.
        """
        text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
        text = re.sub(r"\n?```\s*$", "", text).strip()

        brace = text.find("{")
        if brace > 0:
            text = text[brace:]
        rbrace = text.rfind("}")
        if rbrace != -1 and rbrace < len(text) - 1:
            text = text[: rbrace + 1]

        text = re.sub(r",\s*([\]}])", r"\1", text)

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
                result.append(" ")
            else:
                result.append(char)
        text = "".join(result).strip()

        try:
            json.loads(text)
            return text
        except json.JSONDecodeError:
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

    @staticmethod
    def _format_consensus_directive(state: DebateChamberState) -> str:
        method = state.get("consensus_method") or "pending"
        winner = state.get("consensus_winner") or {}
        votes = state.get("agent_votes") or []
        dissenters = state.get("dissenting_agents") or []
        return (
            f"consensus_reached: {state.get('consensus_reached', False)}\n"
            f"consensus_method: {method}\n"
            f"winner: {json.dumps(winner, ensure_ascii=False)}\n"
            f"dissenting_agents: {json.dumps(dissenters, ensure_ascii=False)}\n"
            f"agent_votes: {json.dumps(votes, ensure_ascii=False)}\n"
            "Rules:\n"
            "- If consensus_method=soft_hold, final rating must be HOLD.\n"
            "- If consensus_method=confidence_winner, align final rating with the winner position.\n"
            "- CIO may validate price levels and risks, but must not override those two consensus outcomes."
        )

    @staticmethod
    def _append_reason(existing: str | None, addition: str) -> str:
        existing_text = str(existing or "").strip()
        if not existing_text:
            return addition
        return f"{existing_text} {addition}"

    def _apply_consensus_override(self, parsed: dict, state: DebateChamberState) -> dict:
        p = dict(parsed) if isinstance(parsed, dict) else {}
        method = state.get("consensus_method")
        winner = state.get("consensus_winner") or {}
        dissenters = list(state.get("dissenting_agents") or [])

        p["consensus_reached"] = bool(state.get("consensus_reached", False))
        p["consensus_method"] = method
        p["dissenting_agents"] = dissenters

        if method == "soft_hold":
            p["rating"] = "HOLD"
            p["confidence"] = min(float(p.get("confidence") or 0.52), 0.55)
            p["weighted_reasoning"] = self._append_reason(
                p.get("weighted_reasoning"),
                (
                    "Consensus override: Bull/Bear confidence gap was below 0.15, "
                    "so the chamber treats this as soft consensus HOLD instead of "
                    "forcing an entry."
                ),
            )
            return p

        if method == "confidence_winner":
            winner_position = self._normalise_position(str(winner.get("position", "HOLD")))
            if winner_position == "UNKNOWN":
                winner_position = "HOLD"
            p["rating"] = "BUY" if winner_position == "BUY" else winner_position
            try:
                p["confidence"] = max(0.0, min(float(winner.get("confidence", p.get("confidence", 0.0))), 1.0))
            except (TypeError, ValueError):
                p["confidence"] = max(0.0, min(float(p.get("confidence") or 0.0), 1.0))
            p["weighted_reasoning"] = self._append_reason(
                p.get("weighted_reasoning"),
                (
                    "Consensus override: no 60% vote after 3 rounds, so "
                    f"{winner.get('agent', 'highest-confidence agent')} wins by "
                    "highest numeric confidence. CIO did not override the winner."
                ),
            )
            return p

        return p

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

        if current_price <= 0:
            logger.warning(f"[CIO] Invalid current price for {ticker}; returning HOLD fallback")
            verdict_json = CIOVerdict(
                ticker=ticker,
                rating="HOLD",
                confidence=0.0,
                summary="Harga pasar tidak valid; trade envelope tidak dibuat.",
                current_price=current_price,
                fair_value=fair_value if fair_value and fair_value > 0 else None,
                entry_price_range=None,
                target_price=None,
                stop_loss=None,
                consensus_reached=bool(state.get("consensus_reached", False)),
                consensus_method=state.get("consensus_method"),
                dissenting_agents=list(state.get("dissenting_agents") or []),
            ).model_dump_json()
            return {"final_verdict": verdict_json}

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
                "SIGNAL: Fundamental ✅ + Technical ✅ → Lean BUY; choose final confidence "
                "using the calibration rubric, caps, and Devil's Advocate penalty. "
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
                "risky; apply the overextended-risk cap and choose the lower end of "
                "the applicable confidence band."
            )

        # ── Build CIO prompt ─────────────────────────────────────────────────
        consensus_directive = self._format_consensus_directive(state)
        hist = "\n".join(
            f"[{m.role.upper()} R{m.round_num}]: {self._redact_debate_prices(m.content)}"
            for m in state["debate_history"]
        )
        decision_context = state.get("decision_brief") or self._compact_text(
            state.get("raw_data", ""),
            3_000,
        )
        user_content = (
            f"Ticker: {ticker}\n"
            f"Current Market Price: Rp {current_price:,.0f}\n\n"
            f"=== TRADE ENVELOPE (Python-Computed — Use VERBATIM) ===\n"
            f"{envelope_text}\n\n"
            f"=== CONFLICT RESOLUTION ===\n"
            f"{conflict_signal}\n\n"
            f"=== CONSENSUS DIRECTIVE ===\n"
            f"{consensus_directive}\n\n"
            f"Decision Brief (compressed, no raw source dump):\n{decision_context}\n\n"
            f"Debate Transcript (price mentions redacted; Trade Envelope is the only price source):\n{hist}\n\n"
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
  "risk_reward_ratio": <float>,
  "consensus_reached": <true | false>,
  "consensus_method": "<voting | confidence_winner | soft_hold>",
  "dissenting_agents": ["<agent>", ...]
}

Start your response with '{' and end with '}'. Nothing else."""

        messages = [
            SystemMessage(content=CIO_SYSTEM_PROMPT + json_schema_hint),
            HumanMessage(content=user_content),
        ]

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
            parsed = json.loads(self._sanitize_json(resp.content))
            parsed = _apply_envelope(parsed)
            parsed = self._apply_consensus_override(parsed, state)
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
                consensus_reached=bool(state.get("consensus_reached", False)),
                consensus_method=state.get("consensus_method"),
                dissenting_agents=list(state.get("dissenting_agents") or []),
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
        market_data = await self._fetch_market_data(ticker)
        if current_price <= 0:
            current_price = derive_current_price(market_data)
        initial_state: DebateChamberState = {
            "ticker": ticker,
            "current_price": current_price,
            "market_data": market_data,
            "fundamental_data": "",
            "technical_data": "",
            "sentiment_data": "",
            "raw_data": "",
            "decision_brief": "",
            "technical_indicators": {},
            "fair_value_estimate": 0.0,
            "debate_history": [],
            "round_count": 0,
            "consensus_reached": False,
            "consensus_method": None,
            "dissenting_agents": [],
            "agent_votes": [],
            "consensus_winner": None,
            "disagreement_type": None,
            "devils_advocate_question": "",
            "final_verdict": "",
            "metadata": {
                "prompt_version": getattr(self, "prompt_version", PROMPT_VERSION),
                "market_data_source": market_data.get("source", "unknown"),
                "market_data_cached": True,
            },
            "error": None,
        }
        logger.info(f"[DebateChamber] ▶ Starting swing-trade pipeline for {ticker} @ Rp {current_price:,.0f}")
        result = await self.app.ainvoke(initial_state)
        logger.info(f"[DebateChamber] ✅ Pipeline complete for {ticker}")
        return result
