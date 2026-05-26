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
from collections.abc import AsyncGenerator
import json
import re
from time import perf_counter
from typing import Any, Literal

import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

try:
    import pytz
    _TZ_WIB = pytz.timezone("Asia/Jakarta")
except Exception:
    from datetime import timezone, timedelta
    _TZ_WIB = timezone(timedelta(hours=7))

from core.budget import (
    BudgetExhaustedError,
    check_and_increment_flash_budget,
    check_and_increment_pro_budget,
)
from core.adaptive_planner import (
    DEFAULT_PLANNER,
    PlannerContext,
    PipelineStage,
    PlanAction,
)
from core.execution_ledger import DEFAULT_LEDGER
from core.failure_taxonomy import classify_exception
from core.handoff_envelope import make_envelope
from core.observation_store import AgentObservation, DEFAULT_STORE
from providers.gemini import get_flash_llm, get_pro_llm
from schemas.debate import (
    CIOVerdict,
    DebateChamberState,
    DebateMessage,
    history_updater,
    validate_swing_targets,
)
from services.context_pack_builder import build_context_pack, pack_to_prompt_string
from services.rag_evidence_store import DEFAULT_STORE as rag_store
from services.fair_value_calculator import build_fair_value_report
from services.debate_prompt_registry import PROMPT_REGISTRY, PROMPT_VERSION
from services.debate_run_guard import run_with_guard
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


def _as_debate_message(m):
    from schemas.debate import DebateMessage
    if isinstance(m, dict):
        return DebateMessage(**m)
    return m


def _ledger_call(action: str, func, *args, **kwargs) -> None:
    try:
        func(*args, **kwargs)
    except Exception as exc:
        logger.warning(f"[ExecutionLedger] {action} failed: {exc}")


def _state_metadata(state: DebateChamberState) -> dict:
    metadata = state.get("metadata") or {}
    return metadata if isinstance(metadata, dict) else {}


def _state_run_id(state: DebateChamberState) -> str:
    return str(_state_metadata(state).get("run_id", "unknown"))


def _state_ticker(state: DebateChamberState) -> str:
    return str(state.get("ticker", "unknown"))


def _state_attempt(state: DebateChamberState, attempt_key: str) -> int:
    try:
        return int(_state_metadata(state).get(attempt_key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _ledger_stage_start(
    state: DebateChamberState,
    *,
    stage: str,
    attempt_key: str,
) -> None:
    _ledger_call(
        f"{stage} stage start",
        DEFAULT_LEDGER.stage_start,
        run_id=_state_run_id(state),
        ticker=_state_ticker(state),
        stage=stage,
        attempt=_state_attempt(state, attempt_key),
    )


def _ledger_stage_success(
    state: DebateChamberState,
    *,
    stage: str,
    started_at: float,
    detail: dict | None = None,
) -> None:
    _ledger_call(
        f"{stage} stage success",
        DEFAULT_LEDGER.stage_success,
        run_id=_state_run_id(state),
        ticker=_state_ticker(state),
        stage=stage,
        duration_ms=int((perf_counter() - started_at) * 1000),
        detail=detail or {},
    )


def _ledger_stage_failure(
    state: DebateChamberState,
    *,
    stage: str,
    started_at: float,
    failure_record: dict | None,
    message: str,
    attempt_key: str,
) -> None:
    record = failure_record or {}
    _ledger_call(
        f"{stage} stage failure",
        DEFAULT_LEDGER.stage_failure,
        run_id=_state_run_id(state),
        ticker=_state_ticker(state),
        stage=stage,
        error_code=str(record.get("error_code") or "UNKNOWN"),
        message=message,
        attempt=_state_attempt(state, attempt_key),
        duration_ms=int((perf_counter() - started_at) * 1000),
    )


def _ledger_stage_partial(
    state: DebateChamberState,
    *,
    stage: str,
    reason: str,
    confidence_penalty: float,
) -> None:
    _ledger_call(
        f"{stage} stage partial",
        DEFAULT_LEDGER.stage_partial,
        run_id=_state_run_id(state),
        ticker=_state_ticker(state),
        stage=stage,
        reason=reason,
        confidence_penalty=confidence_penalty,
    )


def _news_context_for_state(state: DebateChamberState, ticker: str) -> dict[str, object]:
    try:
        from services.news_fetcher import DEFAULT_FETCHER

        news_bundle = DEFAULT_FETCHER.build_bundle(ticker)
        news_str = DEFAULT_FETCHER.bundle_to_prompt_string(news_bundle)
        if news_bundle.confidence_adjustment != 0:
            logger.info(
                f"[News] {ticker}: "
                f"adjustment={news_bundle.confidence_adjustment:+.2f} "
                f"({news_bundle.confidence_adjustment_reason})"
            )
        if news_bundle.has_breaking_news:
            logger.warning(f"[News] {ticker}: BREAKING NEWS DETECTED")

        metadata = dict(state.get("metadata") or {})
        metadata["has_breaking_news"] = news_bundle.has_breaking_news
        metadata["news_confidence_adjustment"] = news_bundle.confidence_adjustment
        metadata["news_overall_sentiment"] = news_bundle.overall_sentiment.value
        metadata["news_brief"] = news_str
        state["news_brief"] = news_str
        state["news_confidence_adjustment"] = news_bundle.confidence_adjustment
        state["metadata"] = metadata
        return {
            "news_brief": news_str,
            "news_confidence_adjustment": news_bundle.confidence_adjustment,
            "metadata": metadata,
        }
    except Exception as exc:
        logger.warning(f"[News] {ticker}: fetch failed: {exc}")
        metadata = dict(state.get("metadata") or {})
        metadata["has_breaking_news"] = False
        metadata["news_confidence_adjustment"] = 0.0
        metadata["news_brief"] = ""
        state["news_brief"] = ""
        state["news_confidence_adjustment"] = 0.0
        state["metadata"] = metadata
        return {
            "news_brief": "",
            "news_confidence_adjustment": 0.0,
            "metadata": metadata,
        }


def _news_adjustment_from_state(state: DebateChamberState) -> float:
    metadata = state.get("metadata") or {}
    raw_adjustment = state.get("news_confidence_adjustment")
    if raw_adjustment in (None, ""):
        raw_adjustment = metadata.get("news_confidence_adjustment", 0.0)
    try:
        return float(raw_adjustment or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _planner_decision_for_state(
    state: DebateChamberState,
    *,
    stage: PipelineStage,
    attempt_key: str,
    failure_record: dict | None = None,
):
    """Run adaptive planner safely for a graph node failure."""
    try:
        metadata = state.get("metadata") or {}
        ctx = PlannerContext(
            ticker=str(state.get("ticker", "unknown")),
            run_id=str(metadata.get("run_id", "unknown")),
            stage=stage,
            attempt=int(metadata.get(attempt_key, 0) or 0),
            failure_record=failure_record,
            provider_health=None,
            observations_count=0,
            batch_failed_count=0,
        )
        decision = DEFAULT_PLANNER.plan(ctx)
        DEFAULT_PLANNER.log_decision(decision)
        logger.info(f"[Planner] {DEFAULT_PLANNER.format_decision(decision)}")
        _ledger_call(
            "planner decision",
            DEFAULT_LEDGER.planner_decision,
            run_id=ctx.run_id,
            ticker=ctx.ticker,
            stage=ctx.stage.name,
            action=decision.action.name,
            reason=decision.reason,
            attempt=ctx.attempt,
        )
        return decision
    except Exception as exc:
        logger.warning(
            f"[Planner] Failed during {stage.value} planning for "
            f"{state.get('ticker', 'unknown')}; using original behavior: {exc}"
        )
        return None


def _metadata_with_planner_note(
    state: DebateChamberState,
    decision,
) -> dict:
    metadata = dict(state.get("metadata") or {})
    if decision is None:
        return metadata

    if decision.context_note:
        notes = list(metadata.get("planner_context_notes") or [])
        notes.append(decision.context_note)
        metadata["planner_context_notes"] = notes

    if decision.confidence_penalty:
        existing_penalty = float(metadata.get("planner_confidence_penalty", 0.0) or 0.0)
        metadata["planner_confidence_penalty"] = round(
            existing_penalty + decision.confidence_penalty,
            4,
        )
        if "confidence" in metadata:
            try:
                metadata["confidence"] = max(
                    0.0,
                    float(metadata["confidence"]) - decision.confidence_penalty,
                )
            except (TypeError, ValueError):
                pass
    return metadata


def _increment_planner_attempt(
    state: DebateChamberState,
    attempt_key: str,
) -> None:
    metadata = dict(state.get("metadata") or {})
    metadata[attempt_key] = int(metadata.get(attempt_key, 0) or 0) + 1
    state["metadata"] = metadata


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
        if stockbit_client is None:
            from services.stockbit_api_client import StockbitApiClient

            stockbit_client = StockbitApiClient()
        self.stockbit_client = stockbit_client
        self.app = self._build_graph()
        self.prompt_version = PROMPT_VERSION
        self._llm_call_counts: dict[tuple[str, str], dict[str, int]] = {}

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
    def _llm_content_to_text(cls, content: Any) -> str:
        """Normalize LangChain/Gemini message content parts into plain text."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            if "text" in content:
                return cls._llm_content_to_text(content.get("text"))
            if "content" in content:
                return cls._llm_content_to_text(content.get("content"))
            return json.dumps(content, ensure_ascii=False)
        if isinstance(content, list | tuple):
            parts = [
                cls._llm_content_to_text(item).strip()
                for item in content
            ]
            return "\n".join(part for part in parts if part)
        for attr in ("text", "content"):
            value = getattr(content, attr, None)
            if value is not None:
                return cls._llm_content_to_text(value)
        return str(content)

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

    def _ensure_signal_footer(self, content: Any, role: str) -> tuple[str, dict[str, object]]:
        text = self._llm_content_to_text(content).strip()
        signal = self._extract_agent_signal(text, role)
        confidence = signal.get("confidence")
        if confidence is None:
            confidence = 0.0
            signal["confidence"] = confidence
        if signal.get("position") == "UNKNOWN":
            signal["position"] = "HOLD" if confidence == 0.0 else "UNKNOWN"

        if "agent confidence" not in text.lower() or "position:" not in text.lower():
            text = (
                f"{text}\n\n"
                f"Position: {signal['position']}\n"
                f"Agent Confidence: {float(confidence):.2f}"
            ).strip()
        return text, signal

    @staticmethod
    def _record_observation(
        state: DebateChamberState,
        agent: str,
        content: str,
        signal: dict[str, object] | None = None,
    ) -> None:
        try:
            metadata = state.get("metadata") or {}
            run_id = str(metadata.get("run_id", "unknown"))
            ticker = str(state.get("ticker", "unknown"))
            confidence = signal.get("confidence") if signal else None
            summary = str(content or "")[:300]
            envelope = make_envelope(
                producer=agent,
                consumer="observation_store",
                ticker=ticker,
                run_id=run_id,
                payload={"summary": summary},
                confidence=confidence if isinstance(confidence, (int, float)) else None,
            )
            DEFAULT_STORE.append(
                AgentObservation(
                    run_id=run_id,
                    ticker=ticker,
                    agent=agent,
                    position=str(signal.get("position", "UNKNOWN")) if signal else "NEUTRAL",
                    confidence=confidence if isinstance(confidence, (int, float)) else None,
                    summary=summary,
                    round_num=int(state.get("round_count", 0) or 0),
                    prompt_version=str(metadata.get("prompt_version", "unknown")),
                    timestamp=envelope.created_at,
                    evidence=[],
                )
            )
        except Exception as exc:
            logger.warning(
                f"[ObservationStore] Failed to append {agent} observation "
                f"for {state.get('ticker', 'unknown')}: {exc}"
            )

    @staticmethod
    def _latest_message(state: DebateChamberState, role: str) -> DebateMessage | None:
        messages = []
        for raw in state.get("debate_history", []):
            message = _as_debate_message(raw)
            if message.role == role:
                messages.append(message)
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

    def _reset_llm_counters(self, state: DebateChamberState) -> None:
        try:
            metadata = state.get("metadata") or {}
            metadata["flash_calls"] = 0
            metadata["pro_calls"] = 0
            state["metadata"] = metadata
            key = (str(metadata.get("run_id", "unknown")), str(state.get("ticker", "unknown")))
            self._llm_call_counts[key] = {"flash_calls": 0, "pro_calls": 0}
        except Exception as exc:
            logger.warning(f"[Telemetry] Failed to initialize LLM counters: {exc}")

    def _record_llm_call(self, state: DebateChamberState | None, tier: str) -> None:
        try:
            if state is None:
                return
            counter_key = "pro_calls" if tier == "pro" else "flash_calls"
            metadata = state.get("metadata") or {}
            metadata[counter_key] = int(metadata.get(counter_key, 0) or 0) + 1
            state["metadata"] = metadata
            key = (str(metadata.get("run_id", "unknown")), str(state.get("ticker", "unknown")))
            counts = self._llm_call_counts.setdefault(
                key,
                {"flash_calls": 0, "pro_calls": 0},
            )
            counts[counter_key] = int(counts.get(counter_key, 0) or 0) + 1
        except Exception as exc:
            logger.warning(f"[Telemetry] Failed to record LLM call: {exc}")

    def _merge_llm_counters(self, result: dict, run_id: str, ticker: str) -> dict:
        try:
            metadata = result.get("metadata") or {}
            key = (run_id, ticker)
            counts = self._llm_call_counts.get(key, {})
            metadata["flash_calls"] = int(counts.get("flash_calls", 0) or 0)
            metadata["pro_calls"] = int(counts.get("pro_calls", 0) or 0)
            result["metadata"] = metadata
        except Exception as exc:
            logger.warning(f"[Telemetry] Failed to merge LLM counters: {exc}")
        return result

    @staticmethod
    def _merge_node_update(
        state: DebateChamberState,
        update: dict[str, Any] | None,
    ) -> DebateChamberState:
        """Merge a node result into state using the debate-history reducer."""

        if not update:
            return state
        for key, value in update.items():
            if key == "debate_history":
                state["debate_history"] = history_updater(
                    state.get("debate_history", []),
                    value,
                )
            else:
                state[key] = value
        return state

    def _new_initial_state(
        self,
        *,
        ticker: str,
        current_price: float,
        market_data: dict[str, Any],
        run_id: str,
    ) -> DebateChamberState:
        """Create the canonical initial debate state."""

        return {
            "ticker": ticker,
            "current_price": current_price,
            "market_data": market_data,
            "fundamental_data": "",
            "technical_data": "",
            "sentiment_data": "",
            "news_brief": "",
            "news_confidence_adjustment": 0.0,
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
                "run_id": run_id,
                "market_data_source": market_data.get("source", "unknown"),
                "market_data_cached": True,
                "flash_calls": 0,
                "pro_calls": 0,
            },
            "error": None,
        }

    @staticmethod
    def _message_field(message: Any, field: str, default: Any = None) -> Any:
        """Read a DebateMessage or dict field."""

        if isinstance(message, dict):
            if field == "round_num" and field not in message:
                return message.get("round", default)
            return message.get(field, default)
        if field == "round_num" and not hasattr(message, field):
            return getattr(message, "round", default)
        return getattr(message, field, default)

    def _public_scout_metrics(
        self,
        state: DebateChamberState,
    ) -> dict[str, dict[str, Any]]:
        """Format scout state into UI-safe metrics."""

        metadata = state.get("metadata") or {}
        return {
            "technical": {
                **(state.get("technical_indicators") or {}),
                "current_price": state.get("current_price", 0.0),
            },
            "fundamental": {
                "fair_value": state.get("fair_value_estimate", 0.0),
                "position": self._extract_agent_signal(
                    str(state.get("fundamental_data", "")),
                    "fundamental_scout",
                ).get("position", "UNKNOWN"),
            },
            "sentiment": {
                "news": metadata.get("news_overall_sentiment", "UNKNOWN"),
                "adjustment": state.get("news_confidence_adjustment", 0.0),
                "position": self._extract_agent_signal(
                    str(state.get("sentiment_data", "")),
                    "sentiment_specialist",
                ).get("position", "UNKNOWN"),
            },
        }

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

    async def _invoke_llm_for_state(
        self,
        state: DebateChamberState,
        llm,
        messages,
        inject_rules: bool = True,
    ):
        tier = self._classify_llm_tier(llm)
        resp = await self._invoke_llm(llm, messages, inject_rules=inject_rules)
        self._record_llm_call(state, tier)
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
        content = self._llm_content_to_text(getattr(resp, "content", None))
        if not content.strip():
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
        started_at = perf_counter()
        _ledger_stage_start(
            state,
            stage="FUNDAMENTAL_FETCH",
            attempt_key="fundamental_attempt",
        )
        try:
            raw = await self._fetch_url(
                f"{BASE_URL}/keystats/ratio/v1/{ticker}?year_limit=10"
            )
            if not raw:
                content = "Data Unavailable"
                self._record_observation(state, "fundamental_scout", content)
                _ledger_stage_success(
                    state,
                    stage="FUNDAMENTAL_FETCH",
                    started_at=started_at,
                    detail={"source": "stockbit", "available": False},
                )
                return {"fundamental_data": content}

            report_str, fv_price = build_fair_value_report(raw, ticker, current_price)
            logger.info(f"[Fundamental] Fair value for {ticker}: {fv_price}")
            if fv_price is None:
                logger.warning(f"[Fundamental] Raw API response for {ticker}: {json.dumps(raw)[:2000]}")

            messages = [
                SystemMessage(content=FUNDAMENTAL_SCOUT_PROMPT + AGENT_SIGNAL_PROMPT),
                HumanMessage(content=f"{report_str}\n\n=== RAW API JSON ===\n{json.dumps(raw)[:10_000]}"),
            ]
            resp = await self._invoke_llm_for_state(state, self.flash_llm, messages)
            content, signal = self._ensure_signal_footer(resp.content, "fundamental_scout")
            self._record_observation(state, "fundamental_scout", content, signal)
            _ledger_stage_success(
                state,
                stage="FUNDAMENTAL_FETCH",
                started_at=started_at,
                detail={"source": "stockbit", "fair_value": fv_price},
            )
            return {
                "fundamental_data": content,
                "fair_value_estimate": fv_price,
            }
        except Exception as e:
            logger.error(f"[Fundamental] Error: {e}")
            failure_record = classify_exception(e, "stockbit").model_dump(mode="json")
            decision = _planner_decision_for_state(
                state,
                stage=PipelineStage.FUNDAMENTAL_FETCH,
                attempt_key="fundamental_attempt",
                failure_record=failure_record,
            )
            _ledger_stage_failure(
                state,
                stage="FUNDAMENTAL_FETCH",
                started_at=started_at,
                failure_record=failure_record,
                message=str(e),
                attempt_key="fundamental_attempt",
            )
            if decision is not None and decision.action is PlanAction.RETRY:
                _increment_planner_attempt(state, "fundamental_attempt")
                raise
            content = "Data Unavailable (Error)"
            self._record_observation(state, "fundamental_scout", content)
            if decision is not None and decision.action is PlanAction.PROCEED_PARTIAL:
                _ledger_stage_partial(
                    state,
                    stage="FUNDAMENTAL_FETCH",
                    reason=decision.context_note or decision.reason,
                    confidence_penalty=decision.confidence_penalty,
                )
                return {
                    "fundamental_data": content,
                    "metadata": _metadata_with_planner_note(state, decision),
                }
            if decision is not None and decision.action is PlanAction.SKIP_TICKER:
                return {
                    "fundamental_data": "",
                    "metadata": _metadata_with_planner_note(state, decision),
                }
            return {"fundamental_data": content}

    async def _chartist_node(self, state: DebateChamberState) -> dict:
        """Chartist with real OHLCV from yfinance — pre-computes all technicals in Python."""
        ticker = state["ticker"]
        logger.info(f"[Chartist] Fetching OHLCV + orderbook for {ticker}")
        started_at = perf_counter()
        technical_partial = False
        _ledger_stage_start(
            state,
            stage="TECHNICAL_FETCH",
            attempt_key="technical_attempt",
        )
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
            failure_record = classify_exception(e, "yfinance").model_dump(mode="json")
            decision = _planner_decision_for_state(
                state,
                stage=PipelineStage.TECHNICAL_FETCH,
                attempt_key="technical_attempt",
                failure_record=failure_record,
            )
            _ledger_stage_failure(
                state,
                stage="TECHNICAL_FETCH",
                started_at=started_at,
                failure_record=failure_record,
                message=str(e),
                attempt_key="technical_attempt",
            )
            if decision is not None and decision.action is PlanAction.RETRY:
                _increment_planner_attempt(state, "technical_attempt")
                raise
            if decision is not None and decision.action is PlanAction.PROCEED_PARTIAL:
                technical_partial = True
                _ledger_stage_partial(
                    state,
                    stage="TECHNICAL_FETCH",
                    reason=decision.context_note or decision.reason,
                    confidence_penalty=decision.confidence_penalty,
                )
                state["metadata"] = _metadata_with_planner_note(state, decision)

        # ── 2. Also fetch orderbook for near-term level context ──────────────
        orderbook_data: dict = {}
        try:
            orderbook_data = await self._fetch_url(
                f"{BASE_URL}/company-price-feed/v2/orderbook/companies/{ticker}"
            ) or {}
        except Exception as e:
            logger.warning(f"[Chartist] Orderbook fetch failed: {e}")
            failure_record = classify_exception(e, "stockbit").model_dump(mode="json")
            decision = _planner_decision_for_state(
                state,
                stage=PipelineStage.TECHNICAL_FETCH,
                attempt_key="technical_attempt",
                failure_record=failure_record,
            )
            _ledger_stage_failure(
                state,
                stage="TECHNICAL_FETCH",
                started_at=started_at,
                failure_record=failure_record,
                message=str(e),
                attempt_key="technical_attempt",
            )
            if decision is not None and decision.action is PlanAction.RETRY:
                _increment_planner_attempt(state, "technical_attempt")
                raise
            if decision is not None and decision.action is PlanAction.PROCEED_PARTIAL:
                technical_partial = True
                _ledger_stage_partial(
                    state,
                    stage="TECHNICAL_FETCH",
                    reason=decision.context_note or decision.reason,
                    confidence_penalty=decision.confidence_penalty,
                )
                state["metadata"] = _metadata_with_planner_note(state, decision)

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
        resp = await self._invoke_llm_for_state(state, self.pro_llm, messages)
        content, signal = self._ensure_signal_footer(resp.content, "chartist")
        self._record_observation(state, "chartist", content, signal)
        if not technical_partial:
            _ledger_stage_success(
                state,
                stage="TECHNICAL_FETCH",
                started_at=started_at,
                detail={"source": "yfinance", "has_technicals": bool(tech_indicators)},
            )
        return {
            "technical_data": content,
            "technical_indicators": tech_indicators,
        }

    async def _sentiment_node(self, state: DebateChamberState) -> dict:
        ticker = state["ticker"]
        logger.info(f"[Sentiment] Fetching for {ticker}")
        started_at = perf_counter()
        _ledger_stage_start(
            state,
            stage="SENTIMENT_FETCH",
            attempt_key="sentiment_attempt",
        )
        await asyncio.sleep(1.0)   # stagger to avoid burst rate-limit
        try:
            raw = await self._fetch_url(
                f"{BASE_URL}/stream/v3/symbol/{ticker}/pinned"
            )
            if not raw:
                content = "Data Unavailable"
                self._record_observation(state, "sentiment_specialist", content)
                _ledger_stage_success(
                    state,
                    stage="SENTIMENT_FETCH",
                    started_at=started_at,
                    detail={"source": "stockbit", "available": False},
                )
                news_update = _news_context_for_state(state, ticker)
                return {"sentiment_data": content, **news_update}
            messages = [
                SystemMessage(content=SENTIMENT_PROMPT + AGENT_SIGNAL_PROMPT),
                HumanMessage(content=json.dumps(raw)[:10_000]),
            ]
            resp = await self._invoke_llm_for_state(state, self.flash_llm, messages)
            content, signal = self._ensure_signal_footer(resp.content, "sentiment_specialist")
            self._record_observation(state, "sentiment_specialist", content, signal)
            _ledger_stage_success(
                state,
                stage="SENTIMENT_FETCH",
                started_at=started_at,
                detail={"source": "stockbit"},
            )
            news_update = _news_context_for_state(state, ticker)
            return {"sentiment_data": content, **news_update}
        except Exception as e:
            logger.error(f"[Sentiment] Error: {e}")
            failure_record = classify_exception(e, "stockbit").model_dump(mode="json")
            decision = _planner_decision_for_state(
                state,
                stage=PipelineStage.SENTIMENT_FETCH,
                attempt_key="sentiment_attempt",
                failure_record=failure_record,
            )
            _ledger_stage_failure(
                state,
                stage="SENTIMENT_FETCH",
                started_at=started_at,
                failure_record=failure_record,
                message=str(e),
                attempt_key="sentiment_attempt",
            )
            content = "Data Unavailable (Error)"
            self._record_observation(state, "sentiment_specialist", content)
            metadata = _metadata_with_planner_note(state, decision)
            state["metadata"] = metadata
            if decision is not None and decision.action is PlanAction.PROCEED_PARTIAL:
                _ledger_stage_partial(
                    state,
                    stage="SENTIMENT_FETCH",
                    reason=decision.context_note or decision.reason,
                    confidence_penalty=decision.confidence_penalty,
                )
            news_update = _news_context_for_state(state, ticker)
            return {
                "sentiment_data": content,
                **news_update,
            }

    async def _synthesizer_node(self, state: DebateChamberState) -> dict:
        """
        Fan-in: merge the three parallel data briefs into one context string.
        Also runs the Margin-of-Safety pre-check and injects any warnings
        so that debate agents are immediately aware of overvaluation risk.
        """
        logger.info("[Synthesizer] Merging parallel data + margin-of-safety check")
        started_at = perf_counter()
        _ledger_stage_start(
            state,
            stage="CONTEXT_BUILD",
            attempt_key="context_build_attempt",
        )
        from utils.exdate_scanner import format_exdate_block

        ticker = state["ticker"]
        f = state.get("fundamental_data", "Missing")
        t = state.get("technical_data", "Missing")
        s = state.get("sentiment_data", "Missing")
        metadata = state.get("metadata") or {}
        news_brief = str(state.get("news_brief") or metadata.get("news_brief") or "")
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

        sources = ["stockbit", "gemini"]
        market_source = (state.get("market_data") or {}).get("source")
        if market_source:
            sources.append(str(market_source))
        if tech:
            sources.append("yfinance")

        context_pack = build_context_pack(
            ticker,
            {
                "current_price": current_price,
                "fair_value_estimate": fair_value_estimate,
                "fundamentals": {
                    "brief": self._compact_text(f, 1_000),
                    "exdate": exdate_block,
                },
                "technicals": {
                    "brief": self._compact_text(t, 1_000),
                    **(tech or {}),
                },
                "sentiment_summary": self._compact_text(s, 800),
                "data_sources": sources,
            },
        )
        if context_pack.missing_fields:
            logger.warning(
                f"[ContextPack] {ticker} missing fields: {context_pack.missing_fields}"
            )
        if context_pack.token_estimate > 2800:
            logger.warning(
                f"[ContextPack] {ticker} token_estimate={context_pack.token_estimate}"
            )
        try:
            run_id = state.get("metadata", {}).get("run_id", "unknown")
            bundle = rag_store.build_bundle(
                pack=context_pack,
                run_id=run_id,
                query_context="swing trade analysis",
            )
            try:
                metadata = state.get("metadata") or {}
                metadata["rag_chunks_selected"] = bundle.total_chunks_selected
                metadata["rag_chunks_considered"] = bundle.total_chunks_considered
                metadata["rag_token_estimate"] = bundle.token_estimate
                state["metadata"] = metadata
            except Exception as exc:
                logger.warning(f"[RAG] Failed to store telemetry metrics: {exc}")
            if bundle.has_stale_data:
                logger.warning(
                    f"[RAG] {ticker} has stale evidence: "
                    f"{bundle.staleness_warning}"
                )
            logger.info(
                f"[RAG] {ticker} evidence: "
                f"{bundle.total_chunks_selected}/"
                f"{bundle.total_chunks_considered} chunks, "
                f"~{bundle.token_estimate} tokens"
            )
            decision_brief = rag_store.bundle_to_prompt_string(bundle)
        except Exception as exc:
            logger.warning(
                f"[RAG] {ticker} evidence selection failed; "
                f"falling back to ContextPack brief: {exc}"
            )
            failure_record = classify_exception(exc, "context_build").model_dump(mode="json")
            decision = _planner_decision_for_state(
                state,
                stage=PipelineStage.CONTEXT_BUILD,
                attempt_key="context_build_attempt",
                failure_record=failure_record,
            )
            _ledger_stage_failure(
                state,
                stage="CONTEXT_BUILD",
                started_at=started_at,
                failure_record=failure_record,
                message=str(exc),
                attempt_key="context_build_attempt",
            )
            metadata = _metadata_with_planner_note(state, decision)
            if decision is not None and decision.action is PlanAction.PROCEED_PARTIAL:
                _ledger_stage_partial(
                    state,
                    stage="CONTEXT_BUILD",
                    reason=decision.context_note or decision.reason,
                    confidence_penalty=decision.confidence_penalty,
                )
                decision_brief = raw
                state["metadata"] = metadata
            else:
                try:
                    decision_brief = pack_to_prompt_string(context_pack)
                except Exception as pack_exc:
                    logger.warning(
                        f"[ContextPack] {ticker} fallback brief failed; "
                        f"using raw data: {pack_exc}"
                    )
                    failure_record = classify_exception(
                        pack_exc,
                        "context_pack",
                    ).model_dump(mode="json")
                    decision = _planner_decision_for_state(
                        state,
                        stage=PipelineStage.CONTEXT_BUILD,
                        attempt_key="context_build_attempt",
                        failure_record=failure_record,
                    )
                    _ledger_stage_failure(
                        state,
                        stage="CONTEXT_BUILD",
                        started_at=started_at,
                        failure_record=failure_record,
                        message=str(pack_exc),
                        attempt_key="context_build_attempt",
                    )
                    metadata = _metadata_with_planner_note(state, decision)
                    if decision is not None and decision.action is PlanAction.PROCEED_PARTIAL:
                        _ledger_stage_partial(
                            state,
                            stage="CONTEXT_BUILD",
                            reason=decision.context_note or decision.reason,
                            confidence_penalty=decision.confidence_penalty,
                        )
                    decision_brief = raw
                    state["metadata"] = metadata
        if news_brief:
            decision_brief = f"{decision_brief}\n\n{news_brief}"
        raw = decision_brief

        rag_metadata = state.get("metadata") or {}
        _ledger_stage_success(
            state,
            stage="CONTEXT_BUILD",
            started_at=started_at,
            detail={
                "rag_chunks": rag_metadata.get("rag_chunks_selected", 0),
                "token_estimate": rag_metadata.get("rag_token_estimate", 0),
            },
        )

        return {
            "raw_data": raw,
            "decision_brief": decision_brief,
            "fair_value_estimate": fair_value_estimate,
            "metadata": state.get("metadata", {}),
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
            debate_history = [
                _as_debate_message(m)
                for m in state["debate_history"]
            ]
            hist = "\n".join(
                f"[{m.role.upper()} R{m.round_num}]: {m.content}"
                for m in debate_history
            )
            content_parts.append(f"\n\nDebate History (may be pruned summary):\n{hist}")

        messages = [
            SystemMessage(content=prompt + AGENT_SIGNAL_PROMPT),
            HumanMessage(content="\n".join(content_parts)),
        ]
        resp = await self._invoke_llm_for_state(state, self.flash_llm, messages)
        content, signal = self._ensure_signal_footer(resp.content, "bull")
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
        self._record_observation(state, "bull", content, signal)
        return {"debate_history": [msg]}

    async def _bearish_node(self, state: DebateChamberState) -> dict:
        ticker = state["ticker"]
        rc = state["round_count"]
        logger.info(f"[Bear] Round {rc + 1} for {ticker}")

        prompt = BEAR_SYSTEM_PROMPT_R1 if rc == 0 else BEAR_SYSTEM_PROMPT_R2

        # Always surface the latest Bull argument for the Bear to attack
        debate_history = [
            _as_debate_message(m)
            for m in state["debate_history"]
        ]
        bull_args = [m.content for m in debate_history if m.role == "bull"]
        last_bull = bull_args[-1] if bull_args else "(no bull argument yet)"

        content_parts = [
            f"Ticker: {ticker}\n\nSynthesized Market Data:\n{state['raw_data']}",
            f"\n\nBull's argument to challenge:\n{last_bull}",
        ]

        if rc > 0:
            bear_args = [m.content for m in debate_history if m.role == "bear"]
            if bear_args:
                content_parts.append(
                    f"\n\nYour own Round 1 argument (DO NOT repeat this):\n{bear_args[-1]}"
                )

        messages = [
            SystemMessage(content=prompt + AGENT_SIGNAL_PROMPT),
            HumanMessage(content="\n".join(content_parts)),
        ]
        resp = await self._invoke_llm_for_state(state, self.flash_llm, messages)  # Use Flash for Bear opening/rebuttal rounds
        new_rc = rc + 1
        content, signal = self._ensure_signal_footer(resp.content, "bear")
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
        self._record_observation(state, "bear", content, signal)
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

        for raw in state["debate_history"]:
            m = _as_debate_message(raw)
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
        debate_history = [
            _as_debate_message(m)
            for m in state["debate_history"]
        ]
        hist = "\n".join(
            f"[{m.role.upper()} R{m.round_num}]: {m.content}"
            for m in debate_history
        )
        decision_context = state.get("decision_brief") or state.get("raw_data", "")
        messages = [
            SystemMessage(content=DEVILS_ADVOCATE_PROMPT),
            HumanMessage(content=f"Decision Brief:\n{decision_context}\n\nDebate:\n{hist}"),
        ]
        resp = await self._invoke_llm_for_state(state, self.flash_llm, messages)
        content, signal = self._ensure_signal_footer(resp.content, "devils_advocate")
        msg = DebateMessage(
            role="devils_advocate",
            content=content,
            round_num=state["round_count"] + 1,
            position=str(signal.get("position", "UNKNOWN")),
            confidence=signal.get("confidence"),
        )
        self._record_observation(state, "devils_advocate", content, signal)
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

    @staticmethod
    def _tick_size_for_price(price: float) -> float:
        """Return the IHSG tick size for a price level."""
        try:
            price = float(price)
        except (TypeError, ValueError):
            return 1.0
        if price < 200:
            return 1.0
        if price < 500:
            return 2.0
        if price < 2000:
            return 5.0
        if price < 5000:
            return 10.0
        return 25.0

    @classmethod
    def _next_tick_above(cls, price: float) -> float:
        """Smallest snapped IHSG price strictly above the provided level."""
        try:
            base = float(price)
        except (TypeError, ValueError):
            base = 0.0
        if base <= 0:
            return 1.0

        candidate = base
        for _ in range(10):
            candidate = snap_to_tick(candidate + cls._tick_size_for_price(candidate))
            if candidate > base:
                return candidate
        return base + max(cls._tick_size_for_price(base), 1.0)

    @classmethod
    def _previous_tick_below(cls, price: float) -> float:
        """Largest snapped IHSG price strictly below the provided level."""
        try:
            base = float(price)
        except (TypeError, ValueError):
            return 0.0
        if base <= 0:
            return 0.0

        candidate = base
        for _ in range(10):
            candidate = snap_to_tick(candidate - cls._tick_size_for_price(candidate))
            if 0 < candidate < base:
                return candidate
            if candidate <= 0:
                break
        return max(base - max(cls._tick_size_for_price(base), 1.0), 0.0)

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
        if entry_low >= entry_high:
            entry_high = self._next_tick_above(entry_low)

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
            stop = self._previous_tick_below(entry_low)

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
        if target <= entry_high:
            target = self._next_tick_above(entry_high)

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
        started_at = perf_counter()
        _ledger_stage_start(
            state,
            stage="CIO_VERDICT",
            attempt_key="cio_verdict_attempt",
        )

        if current_price <= 0:
            logger.warning(f"[CIO] Invalid current price for {ticker}; returning HOLD fallback")
            fallback_verdict = CIOVerdict(
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
            )
            verdict_json = fallback_verdict.model_dump_json()
            _ledger_stage_success(
                state,
                stage="CIO_VERDICT",
                started_at=started_at,
                detail={
                    "rating": fallback_verdict.rating,
                    "confidence": fallback_verdict.confidence,
                },
            )
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
        debate_history = [
            _as_debate_message(m)
            for m in state["debate_history"]
        ]
        hist = "\n".join(
            f"[{m.role.upper()} R{m.round_num}]: {self._redact_debate_prices(m.content)}"
            for m in debate_history
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

        def _apply_news_adjustment(parsed: dict) -> dict:
            news_adj = _news_adjustment_from_state(state)
            if news_adj == 0:
                return parsed
            logger.info(
                f"[News] Applying confidence adjustment {news_adj:+.2f} "
                f"to CIO verdict for {ticker}"
            )
            p = dict(parsed)
            try:
                confidence = float(p.get("confidence") or 0.0)
            except (TypeError, ValueError):
                confidence = 0.0
            p["confidence"] = max(0.0, min(1.0, confidence + news_adj))
            p["weighted_reasoning"] = self._append_reason(
                p.get("weighted_reasoning"),
                f"News sentiment confidence adjustment applied: {news_adj:+.2f}.",
            )
            return p

        try:
            resp = await self._invoke_llm_for_state(
                state,
                self.pro_llm,
                messages,
                inject_rules=False,
            )
            parsed = json.loads(
                self._sanitize_json(self._llm_content_to_text(resp.content))
            )
            parsed = _apply_envelope(parsed)
            parsed = self._apply_consensus_override(parsed, state)
            parsed = _apply_news_adjustment(parsed)
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
        try:
            verdict_detail = json.loads(verdict_json)
        except Exception:
            verdict_detail = {}
        _ledger_stage_success(
            state,
            stage="CIO_VERDICT",
            started_at=started_at,
            detail={
                "rating": verdict_detail.get("rating"),
                "confidence": verdict_detail.get("confidence"),
            },
        )
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

    async def _run_scouts(self, ticker: str) -> dict[str, Any]:
        """
        Run specialist scouts and return technical/fundamental/sentiment metrics.
        """

        market_data = await self._fetch_market_data(ticker)
        current_price = derive_current_price(market_data)
        state = self._new_initial_state(
            ticker=ticker,
            current_price=current_price,
            market_data=market_data,
            run_id=getattr(self, "run_id", "unknown"),
        )
        self._reset_llm_counters(state)

        scout_states = [dict(state), dict(state), dict(state)]
        updates = await asyncio.gather(
            self._fundamental_node(scout_states[0]),
            self._chartist_node(scout_states[1]),
            self._sentiment_node(scout_states[2]),
        )
        for update in updates:
            self._merge_node_update(state, update)

        metrics = self._public_scout_metrics(state)
        metrics["_state"] = state
        return metrics

    async def _run_single_round(
        self,
        state: dict[str, Any],
        round_num: int,
    ) -> dict[str, Any]:
        """
        Run one bull-vs-bear debate round and return arguments plus state.
        """

        debate_state = state
        if round_num > 1:
            self._merge_node_update(
                debate_state,
                await self._state_cleaner_node(debate_state),
            )

        self._merge_node_update(
            debate_state,
            await self._bullish_node(debate_state),
        )
        self._merge_node_update(
            debate_state,
            await self._bearish_node(debate_state),
        )
        self._merge_node_update(
            debate_state,
            await self._consensus_evaluator_node(debate_state),
        )

        bull_msg = self._latest_message(debate_state, "bull")
        bear_msg = self._latest_message(debate_state, "bear")
        bull_conf = float(getattr(bull_msg, "confidence", 0.0) or 0.0)
        bear_conf = float(getattr(bear_msg, "confidence", 0.0) or 0.0)
        return {
            "bull": getattr(bull_msg, "content", "") if bull_msg else "",
            "bear": getattr(bear_msg, "content", "") if bear_msg else "",
            "score_delta": round((bull_conf - bear_conf) * 100),
            "state": debate_state,
        }

    async def _build_verdict(self, state: dict[str, Any]) -> dict[str, Any]:
        """Build the raw final verdict state."""

        debate_state = state
        self._merge_node_update(
            debate_state,
            await self._cio_judge_node(debate_state),
        )
        return self._merge_llm_counters(
            debate_state,
            str((debate_state.get("metadata") or {}).get("run_id", "unknown")),
            str(debate_state.get("ticker", "unknown")),
        )

    def _is_consensus(self, state: dict[str, Any]) -> bool:
        """Return True when the existing consensus threshold has been crossed."""

        if state.get("consensus_reached"):
            return True
        if state.get("consensus_method") in {"soft_hold", "confidence_winner"}:
            return True

        bull_msg = self._latest_message(state, "bull")
        bear_msg = self._latest_message(state, "bear")
        if bull_msg is None or bear_msg is None:
            return False
        bull_conf = getattr(bull_msg, "confidence", None)
        bear_conf = getattr(bear_msg, "confidence", None)
        if bull_conf is None or bear_conf is None:
            return False
        threshold = getattr(self, "consensus_threshold", CONSENSUS_THRESHOLD)
        return abs(float(bull_conf) - float(bear_conf)) >= threshold

    def _init_state(
        self,
        ticker: str,
        scout_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        """Initialize debate state from scout metrics."""

        state = scout_metrics.get("_state")
        if isinstance(state, dict):
            return state
        return self._new_initial_state(
            ticker=ticker,
            current_price=float(
                (scout_metrics.get("technical") or {}).get("current_price", 0.0)
            ),
            market_data={},
            run_id=getattr(self, "run_id", "unknown"),
        )

    async def stream_run(
        self,
        ticker: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream the debate pipeline node-by-node as SSE-friendly event dicts.
        """

        ticker = ticker.strip().upper()
        try:
            yield {
                "type": "progress",
                "ticker": ticker,
                "phase": "scouting",
                "pct": 10,
            }
            await asyncio.sleep(0)

            scout_metrics = await self._run_scouts(ticker)
            public_scout_metrics = {
                key: value
                for key, value in scout_metrics.items()
                if not key.startswith("_")
            }
            yield {
                "type": "scout",
                "ticker": ticker,
                "metrics": public_scout_metrics,
            }
            await asyncio.sleep(0)

            state = self._init_state(ticker, scout_metrics)
            yield {
                "type": "progress",
                "ticker": ticker,
                "phase": "context",
                "pct": 20,
            }
            await asyncio.sleep(0)
            self._merge_node_update(state, await self._synthesizer_node(state))

            total_round_slots = max(MAX_DEBATE_ROUNDS, 1)
            while True:
                next_round = int(state.get("round_count", 0) or 0) + 1
                round_pct = 20 + int((next_round / total_round_slots) * 60)
                yield {
                    "type": "progress",
                    "ticker": ticker,
                    "phase": f"round_{next_round}",
                    "pct": min(round_pct, 80),
                }
                await asyncio.sleep(0)

                round_result = await self._run_single_round(state, next_round)
                state = round_result["state"]
                yield {
                    "type": "round",
                    "ticker": ticker,
                    "data": {
                        "round": next_round,
                        "bull_argument": round_result["bull"],
                        "bear_argument": round_result["bear"],
                        "score_delta": round_result["score_delta"],
                    },
                }
                await asyncio.sleep(0)

                if post_evaluator_router(state) == "devils_advocate":
                    break

            self._merge_node_update(
                state,
                await self._devils_advocate_node(state),
            )
            yield {
                "type": "devil_advocate", 
                "ticker": ticker,
                "question": state.get("devils_advocate_question", "")
            }
            await asyncio.sleep(0)

            yield {
                "type": "progress",
                "ticker": ticker,
                "phase": "verdict",
                "pct": 90,
            }
            await asyncio.sleep(0)

            raw_result = await self._build_verdict(state)
            from app.api.result_adapter import adapt_result

            yield {
                "type": "verdict",
                "ticker": ticker,
                "result": adapt_result(ticker, raw_result),
                "raw_state": raw_result,
            }
            await asyncio.sleep(0)

            yield {"type": "done", "ticker": ticker}
            await asyncio.sleep(0)
        except Exception as exc:
            yield {"type": "error", "ticker": ticker, "message": str(exc)}
            await asyncio.sleep(0)

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
            "news_brief": "",
            "news_confidence_adjustment": 0.0,
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
                "run_id": getattr(self, "run_id", "unknown"),
                "market_data_source": market_data.get("source", "unknown"),
                "market_data_cached": True,
                "flash_calls": 0,
                "pro_calls": 0,
            },
            "error": None,
        }
        self._reset_llm_counters(initial_state)
        logger.info(f"[DebateChamber] ▶ Starting swing-trade pipeline for {ticker} @ Rp {current_price:,.0f}")
        guarded = await run_with_guard(
            ticker=ticker,
            coro=self.app.ainvoke(initial_state),
            timeout_seconds=300,
        )
        if guarded["status"] != "ok":
            logger.error(f"[DebateChamber] Guard failed for {ticker}: {guarded}")
            return {
                **initial_state,
                "error": guarded["error"],
                "metadata": {
                    **initial_state["metadata"],
                    "guard_status": guarded["status"],
                },
            }
        result = guarded["result"]
        result = self._merge_llm_counters(
            result,
            str(initial_state["metadata"].get("run_id", "unknown")),
            ticker,
        )
        logger.info(f"[DebateChamber] ✅ Pipeline complete for {ticker}")
        return result
