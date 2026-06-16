"""
schemas/debate.py — State & Schema definitions for the IHSG Swing Trade Debate Chamber.

Swing Trade update (this session):
- CIOVerdict rebuilt for 1-3 month swing trade frame:
    • fair_value, entry_price_range, target_price, stop_loss (concrete prices)
    • expected_return auto-calculated from entry midpoint → target_price
    • is_overvalued auto-flag follows risk_overvalued (range-aware when available)
    • risk_reward_ratio auto-calculated; rating forced to HOLD/AVOID when < 1.0
    • wait_and_see kept (confidence < 0.60 gate)
- DebateChamberState gains `current_price` field for margin-of-safety logic
- SwingTradeValidator helper: standalone function for the Synthesizer / CIO nodes
"""

import re
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from utils.trade_math import calculate_rr


# ---------------------------------------------------------------------------
# Base helper
# ---------------------------------------------------------------------------


class BaseDataClass(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ---------------------------------------------------------------------------
# Core message type
# ---------------------------------------------------------------------------


class DebateMessage(BaseDataClass):
    """Single argument in the stock debate chamber."""

    role: Literal[
        "scout",
        "bull",
        "bear",
        "synthesizer",
        "devils_advocate",
        "system",
    ] = "scout"
    content: str = ""
    round_num: int = 0
    position: Literal["BUY", "HOLD", "AVOID", "UNKNOWN"] = "UNKNOWN"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


ConsensusMethod = Literal["voting", "confidence_winner", "soft_hold", "deadlock_hold"]


# ---------------------------------------------------------------------------
# CIO Verdict — Swing Trade edition, Pydantic-validated, Svelte-ready
# ---------------------------------------------------------------------------


class CIOVerdict(BaseDataClass):
    """
    Structured output from the CIO Judge — Swing Trade frame (1-3 months, 3-10% target).

    Auto-computed fields (model_validator, never sent by the LLM):
        expected_return   — % gain from entry_mid to target_price
        risk_reward_ratio — calculate_rr from entry_high (worst-case fill):
                            (target - entry_high) / (entry_high - stop).
                            Does NOT equal expected_return / stop_loss_pct,
                            which use the entry-midpoint basis.
        is_overvalued     — follows risk_overvalued for backward compatibility
        wait_and_see      — confidence < 0.60  OR  risk_reward_ratio < 1.0

    Used with LangChain's `.with_structured_output()`.
    Svelte reads this JSON directly — field names are the UI contract.
    """

    # ── Identity ─────────────────────────────────────────────────────────────
    ticker: str = ""

    # ── Core verdict ─────────────────────────────────────────────────────────
    rating: Literal["STRONG_BUY", "BUY", "HOLD", "SELL", "AVOID"] = "HOLD"

    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="CIO confidence in the verdict, 0-1.",
    )

    # ── Swing-trade price levels (LLM must supply these) ─────────────────────
    fair_value: float | None = Field(
        default=None,
        description=(
            "Intrinsic value calculated from Relative Valuation. "
            "Pass null if INSUFFICIENT_DATA or 0."
        ),
    )
    fair_value_base: float | None = Field(
        default=None,
        description="Base fair value used for scoring; same value as fair_value.",
    )
    fair_value_low: float | None = Field(
        default=None,
        description="Conservative low end of the fair value range.",
    )
    fair_value_high: float | None = Field(
        default=None,
        description="Optimistic high end of the fair value range.",
    )
    # FIX: ISSUE 1 — Carry unverified valuation state into final artifacts.
    valuation_gap: str | None = Field(
        default=None,
        description="Set to 'unverified' when fair value is rejected by evidence checks.",
    )

    entry_price_range: str | None = Field(
        default=None,
        description="Safe accumulation zone. Format: 'XXXX - YYYY'. Null if invalid.",
    )

    target_price: float | None = Field(
        default=None,
        description="Swing-trade profit target for 1-3 months. Null if void.",
    )

    stop_loss: float | None = Field(
        default=None,
        description="Hard cut-loss price. Null if void.",
    )

    current_price: float | None = Field(
        default=None,
        description="Last traded price at analysis time (IDR).",
    )

    # ── Narrative fields (LLM must supply these) ──────────────────────────────
    timeframe: str = Field(default="1-3 Months")

    weighted_reasoning: str = Field(
        default="",
        description=(
            "CIO's explicit explanation of how Technical entry logic and "
            "Fundamental fair value were combined to reach this verdict."
        ),
    )

    critical_risk_factor: str = Field(
        default="",
        description="The single factor most likely to invalidate this swing trade.",
    )

    key_catalysts: list[str] = Field(
        default_factory=list,
        description="Top 2-3 reasons the trade should work within 1-3 months.",
    )

    key_risks: list[str] = Field(
        default_factory=list,
        description="Top 2-3 risks that could trigger the stop-loss.",
    )

    summary: str = Field(
        default="",
        description="3-sentence executive summary for the Svelte trade card.",
    )

    # ── Auto-computed (never sent by LLM; derived post-validation) ────────────
    target_basis: str | None = Field(
        default=None,
        description="Basis for the swing profit target price (e.g. '20-day high resistance').",
    )

    expected_return: str | None = Field(
        default=None,
        description="Auto-calculated: % gain from entry midpoint to target_price. null if invalid.",
    )

    risk_reward_ratio: float | None = Field(
        default=None,
        description=(
            "Auto-calculated from entry_high (worst-case fill): "
            "(target_price - entry_high) / (entry_high - stop_loss). "
            "Will NOT equal expected_return / max_risk, which use the "
            "entry-midpoint basis. null if invalid."
        ),
    )

    is_overvalued: bool | None = Field(
        default=None,
        description="Backward-compatible auto-flag; mirrors risk_overvalued.",
    )

    risk_overvalued: bool | None = Field(
        default=None,
        description="Risk flag: True only when current_price is above fair_value_high.",
    )

    wait_and_see: bool = Field(
        default=False,
        description=(
            "Auto-flag: True when confidence < 0.60 OR risk_reward_ratio < 1.0. "
            "Svelte renders a yellow caution banner when this is True."
        ),
    )

    consensus_reached: bool = Field(
        default=False,
        description="Whether the debate agents reached consensus before the CIO formatter.",
    )

    consensus_method: ConsensusMethod | None = Field(
        default=None,
        description="Consensus path used by the debate chamber.",
    )

    dissenting_agents: list[str] = Field(
        default_factory=list,
        description="Agents whose position differed from the consensus or winner position.",
    )

    @model_validator(mode="after")
    def _derive_computed_fields(self) -> "CIOVerdict":
        """
        All business-logic enforcement lives here so the LLM never has to compute
        percentages correctly — it just supplies raw prices.

        Design contract with debate_chamber.py:
          _apply_envelope() in the CIO node overwrites entry_price_range,
          target_price, stop_loss, and fair_value with Python-computed values
          BEFORE this validator runs.  This validator must therefore NEVER erase
          those fields — doing so would silently discard real market data that
          was computed from live OHLCV.

        Change log (bug fixes):
          BUG A — Step 7 (old) stripped envelope prices when rating=HOLD.
                  Removed: prices are always preserved regardless of rating.
          BUG B — Step 5 (old) forced HOLD when gain_pct > 10%.
                  Removed: debate_chamber already caps target via fair-value
                  blending in _compute_trade_envelope; double-penalising here
                  only hides valid momentum trades.
          BUG C — wait_and_see and rating downgrade were triggered solely by
                  fair_value=None, which is common for IHSG stocks with
                  incomplete Stockbit data.  Now only genuinely bad R/R (<1.0)
                  or low confidence (<0.60) triggers wait_and_see; missing
                  fair_value adds a caution note but does not force HOLD.
          BUG D — _parse_entry_mid used a bare str.split('-') which fails for
                  ranges like '48000 - 50000' when a stray minus appears in
                  the string.  Now uses a regex to extract the two numbers.
        """
        # 1. Parse entry midpoint from 'XXXX - YYYY' string
        entry_mid = self._parse_entry_mid()

        # 2. Expected return (entry mid → target)
        if entry_mid > 0 and self.target_price is not None and self.target_price > 0:
            gain_pct = ((self.target_price - entry_mid) / entry_mid) * 100
            self.expected_return = f"{gain_pct:+.1f}%"
        else:
            self.expected_return = None
            gain_pct = 0.0

        # 3. Risk/reward ratio
        entry_bounds = self._parse_entry_bounds()
        if (
            entry_bounds is not None
            and self.stop_loss is not None
            and self.stop_loss > 0
            and self.target_price is not None
            and self.target_price > 0
        ):
            _entry_low, entry_high = entry_bounds
            try:
                self.risk_reward_ratio = calculate_rr(
                    entry_high,
                    self.target_price,
                    self.stop_loss,
                )
            except ValueError:
                self.risk_reward_ratio = None
        else:
            self.risk_reward_ratio = None

        # 4. Overvaluation flag - range-aware margin-of-safety check.
        if self.fair_value_base is None and self.fair_value is not None:
            self.fair_value_base = self.fair_value
        if self.fair_value is None and self.fair_value_base is not None:
            self.fair_value = self.fair_value_base

        if self.current_price is not None and self.current_price > 0:
            if self.fair_value_high is not None and self.fair_value_high > 0:
                self.risk_overvalued = self.current_price > self.fair_value_high
            elif self.fair_value is not None and self.fair_value > 0:
                self.risk_overvalued = self.current_price > self.fair_value
            else:
                self.risk_overvalued = False
        else:
            self.risk_overvalued = False
        self.is_overvalued = self.risk_overvalued

        # 5. Rating downgrade guard — only trigger on genuinely bad R/R.
        #    Missing fair_value is noted via wait_and_see but does NOT force
        #    a downgrade: many IHSG small-caps have incomplete Stockbit data
        #    yet are technically valid swing setups.
        bad_rr = self.risk_reward_ratio is not None and self.risk_reward_ratio < 1.0
        if gain_pct < 3.0 or bad_rr:
            if self.rating in ("STRONG_BUY", "BUY"):
                self.rating = "HOLD"

        # 6. Wait-and-see gate — caution banner in Svelte UI.
        #    Triggered only by low confidence or bad R/R (BUG C completion: missing
        #    fair_value no longer forces this flag — it was causing 80%+ of non-BUY
        #    verdicts to show the caution banner because many IHSG stocks lack
        #    Stockbit fair-value data).  Missing FV still appends a key_risk note.
        missing_fv = self.fair_value is None or self.fair_value <= 0
        if self.confidence < 0.60 or bad_rr:
            self.wait_and_see = True
        if missing_fv and not any(
            "fundamental" in s.lower() for s in self.key_risks
        ):
            self.key_risks = list(self.key_risks) + [
                "Fair value tidak tersedia — validasi fundamental "
                "secara manual sebelum entry."
            ]

        # 7. ── PRICES ARE ALWAYS PRESERVED ──────────────────────────────────
        #    The old code erased target_price / stop_loss / entry_price_range
        #    for HOLD/AVOID ratings.  This caused the Svelte trade card to show
        #    empty levels even though Python computed valid ones from live OHLCV.
        #    Prices are now kept so the UI can always display the trade setup;
        #    the rating + wait_and_see flag already communicate the caution signal.

        if (
            entry_bounds is not None
            and self.stop_loss is not None
            and self.target_price is not None
            and self.stop_loss > 0
            and self.target_price > 0
        ):
            entry_low, entry_high = entry_bounds
            if not (self.stop_loss < entry_low <= entry_high < self.target_price):
                raise ValueError(
                    "Invalid swing price ordering: expected "
                    "stop_loss < entry_low <= entry_high < target_price"
                )

        return self

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _parse_entry_bounds(self) -> tuple[float, float] | None:
        if not self.entry_price_range:
            return None
        try:
            text = re.sub(r"[Rr][Pp]\.?\s*", "", self.entry_price_range).strip()
            parts = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
            if len(parts) < 2:
                return None

            def _to_float(value: str) -> float:
                value = value.strip()
                value = re.sub(r"\.(?=\d{3}(?!\d))", "", value)
                value = value.replace(",", "")
                return float(value)

            return _to_float(parts[0]), _to_float(parts[1])
        except Exception:
            return None

    def _parse_entry_mid(self) -> float:
        """
        Parse 'XXXX - YYYY' → midpoint float.  Returns 0.0 on failure.

        Handles all IHSG price formats:
          • '4800 - 5000'          → plain integers
          • '4.800 - 5.000'        → dot as thousand separator (Indonesian)
          • '4,800 - 5,000'        → comma as thousand separator
          • '4800.0 - 5000.0'      → float notation

        Strategy: extract all digit sequences, reconstruct the numeric value
        by stripping separator characters, then convert to float.
        """
        if not self.entry_price_range:
            return 0.0
        try:
            # Remove currency prefix and whitespace
            text = re.sub(r"[Rr][Pp]\.?\s*", "", self.entry_price_range).strip()
            # Split on the dash that separates the two price levels.
            # Use a greedy split on ' - ' or '-' surrounded by spaces so we
            # don't accidentally split a negative number (not applicable for
            # IHSG prices but keeps the parser generic).
            parts = re.split(r"\s*[-–—]\s*", text, maxsplit=1)
            if len(parts) < 2:
                return 0.0

            # Strip thousand separators (both dot and comma) then parse
            def _to_float(s: str) -> float:
                s = s.strip()
                # If it looks like Indonesian thousand-dot format (e.g. "4.800")
                # the dot is a separator, not a decimal point.
                # Heuristic: if there's exactly one dot and the part after it
                # is exactly 3 digits, treat it as thousand separator.
                s = re.sub(r"\.(?=\d{3}(?!\d))", "", s)  # remove thousand dots
                s = s.replace(",", "")  # remove thousand commas
                return float(s)

            lo = _to_float(parts[0])
            hi = _to_float(parts[1])
            return (lo + hi) / 2
        except Exception:
            return 0.0

    def to_trade_card(self) -> dict:
        """
        Convenience method: returns the minimal dict the Svelte trade card needs.
        Call this in the API response handler instead of model_dump() if you want
        a leaner payload.
        """
        return {
            "ticker": self.ticker,
            "rating": self.rating,
            "buy_at": self.entry_price_range,
            "sell_at": self.target_price,
            "cut_loss": self.stop_loss,
            "fair_value": self.fair_value,
            "fair_value_base": self.fair_value_base,
            "fair_value_low": self.fair_value_low,
            "fair_value_high": self.fair_value_high,
            "expected_return": self.expected_return,
            "risk_reward": self.risk_reward_ratio,
            "is_overvalued": self.is_overvalued,
            "risk_overvalued": self.risk_overvalued,
            "wait_and_see": self.wait_and_see,
            "confidence": self.confidence,
            "summary": self.summary,
            "critical_risk": self.critical_risk_factor,
        }


# ---------------------------------------------------------------------------
# Standalone validator (used by Synthesizer node — no LLM call needed)
# ---------------------------------------------------------------------------


def validate_swing_targets(
    current_price: float,
    fair_value: float,
    target_price: float,
    entry_price_range: str,
    stop_loss: float,
    fair_value_high: float | None = None,
) -> dict:
    """
    Pure-Python margin-of-safety check injected by the Synthesizer node
    BEFORE the debate starts.  Returns a warning string the agents can read.

    This keeps the token-expensive Pro model focused on reasoning,
    not arithmetic.
    """
    warnings: list[str] = []

    # Overvaluation: prefer fair_value_high when available, so prices still
    # inside the valuation range do not create a hard warning.
    uses_high_bound = fair_value_high is not None and fair_value_high > 0
    overvaluation_threshold = fair_value_high if uses_high_bound else fair_value
    if overvaluation_threshold > 0 and current_price > overvaluation_threshold:
        premium = (
            (current_price - overvaluation_threshold) / overvaluation_threshold
        ) * 100
        threshold_label = "fair value high" if uses_high_bound else "fair value"
        warnings.append(
            f"⚠️ OVERVALUED: Current price ({current_price:,.0f}) is "
            f"{premium:.1f}% above {threshold_label} "
            f"({overvaluation_threshold:,.0f}). "
            "Swing trade is HIGH RISK — margin of safety is negative."
        )

    # Profit range gate
    try:
        parts = [p.strip().replace(",", "") for p in entry_price_range.split("-")]
        entry_mid = (float(parts[0]) + float(parts[1])) / 2
        if target_price > 0 and entry_mid > 0:
            gain_pct = ((target_price - entry_mid) / entry_mid) * 100
            if gain_pct < 3.0:
                warnings.append(
                    f"⚠️ LOW UPSIDE: Projected gain ({gain_pct:.1f}%) is below the "
                    "3% swing-trade minimum. Consider a different entry or target."
                )
            elif gain_pct > 10.0:
                warnings.append(
                    f"⚠️ AGGRESSIVE TARGET: Projected gain ({gain_pct:.1f}%) exceeds "
                    "10%. Verify target is below a strong resistance level."
                )
    except Exception:
        pass

    # R/R check
    if stop_loss > 0 and fair_value > 0:
        try:
            loss_pct = ((entry_mid - stop_loss) / entry_mid) * 100
            gain_pct_f = ((target_price - entry_mid) / entry_mid) * 100
            rr = gain_pct_f / loss_pct if loss_pct > 0 else 0
            if rr < 1.0:
                warnings.append(
                    f"⚠️ POOR R/R: Risk/Reward ratio is {rr:.2f} (below 1.0). "
                    "The potential loss exceeds the potential gain."
                )
        except Exception:
            pass

    return {
        "is_valid": len(warnings) == 0,
        "warnings": warnings,
        "warning_text": "\n".join(warnings)
        if warnings
        else "✅ Swing trade parameters within acceptable range.",
    }


# ---------------------------------------------------------------------------
# Custom reducer
# ---------------------------------------------------------------------------


def history_updater(
    left: list[DebateMessage] | None,
    right: list[DebateMessage] | None,
) -> list[DebateMessage]:
    """
    Append-by-default reducer for debate_history.

    Special case: if the first message in `right` has round_num == -1,
    it triggers a *replacement* (used by the State Cleaner node to prune
    the context window and prevent bloat).
    """
    left_list = left or []
    right_list = right or []
    if not right_list:
        return left_list
    if right_list[0].round_num == -1:
        # The sentinel message is discarded; the rest become the new history.
        return right_list[1:]
    return left_list + right_list


def metadata_updater(
    left: dict[str, Any] | None,
    right: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge metadata emitted by parallel LangGraph nodes into one state value."""
    if left is not None and not isinstance(left, dict):
        raise TypeError(
            f"metadata reducer expected dict or None for left, got {type(left).__name__}"
        )
    if right is not None and not isinstance(right, dict):
        raise TypeError(
            f"metadata reducer expected dict or None for right, got {type(right).__name__}"
        )

    merged: dict[str, Any] = dict(left or {})
    for key, value in (right or {}).items():
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = {**existing, **value}
        elif isinstance(existing, list) and isinstance(value, list):
            merged[key] = _merge_metadata_lists(existing, value)
        else:
            merged[key] = value
    return merged


def _merge_metadata_lists(left: list[Any], right: list[Any]) -> list[Any]:
    """Return a stable list union for metadata list fields such as reasons."""
    merged = list(left)
    for item in right:
        if item not in merged:
            merged.append(item)
    return merged


# ---------------------------------------------------------------------------
# LangGraph State
# ---------------------------------------------------------------------------


class DebateChamberState(TypedDict):
    """
    Canonical LangGraph state for the IHSG Swing Trade Debate Chamber.

    New field vs. previous version:
        current_price — last traded price, injected at run() time.
                        Used by the Synthesizer for the margin-of-safety warning
                        and passed through to CIOVerdict for auto-validation.

    Reducer notes
    -------------
    - debate_history  → history_updater  (append + prune support)
    - All other fields → default (last-write wins)
    """

    # Identity
    ticker: str
    current_price: float  # ← NEW: last traded price (IDR)
    market_data: dict  # Cached yfinance data fetched once per ticker

    # Parallel data collection outputs (Phase 1)
    fundamental_data: str
    technical_data: str
    sentiment_data: str
    news_brief: str
    news_confidence_adjustment: float

    # Merged string fed into the debate (includes margin-of-safety warnings)
    raw_data: str
    decision_brief: str

    # Pre-computed technical indicators from yfinance (Python ground truth)
    # Keys: current_price, sma20, ma50, ma200, rsi14, atr14, avg_volume_20d, 52w_high, 52w_low
    technical_indicators: dict

    # Parsed fair value estimate for CIO trade envelope computation
    fair_value_estimate: float
    fair_value_base: float | None
    fair_value_low: float | None
    fair_value_high: float | None
    fair_value_range_pct: float | None
    risk_overvalued: bool

    # Debate engine
    debate_history: Annotated[list[DebateMessage], history_updater]
    round_count: int
    consensus_reached: bool
    consensus_method: ConsensusMethod | None
    dissenting_agents: list[str]
    agent_votes: list[dict]
    consensus_winner: dict | None
    disagreement_type: str | None

    # Adaptive nodes
    devils_advocate_question: str

    # Final output
    final_verdict: str  # JSON-serialized CIOVerdict
    metadata: Annotated[dict, metadata_updater]

    # Error propagation
    error: str | None
