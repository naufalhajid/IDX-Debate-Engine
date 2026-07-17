"""Pure point-in-time fill, maturation, and outcome-backfill machinery.

This module evaluates frozen bars supplied by a caller.  It never downloads
prices, reads the live cache, mutates backtest memory, or submits an order.
The evaluator is deliberately conservative: missing sessions, unadjusted
corporate actions, and future bars fail closed instead of being inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
from typing import Literal, Protocol, Sequence

from pydantic import Field, field_validator, model_validator

from .calendar import (
    IDX_TIMEZONE,
    SESSION_OPEN,
    TRADING_CALENDAR_VERSION,
    TradingCalendar,
    canonical_trading_calendar_sha256,
    session_close_at,
)
from .contracts import (
    CanonicalTicker,
    CostAssumptions,
    LabelDefinition,
    RecordedTradeGeometry,
    Sha256,
    ShadowContractError,
    ShadowDecision,
    ShadowObservation,
    ShadowOutcome,
    ShadowProtocolManifest,
    _StrictFrozenModel,
    _verify_protocol_component,
    canonical_outcome_id,
    canonical_sha256,
)
from .evidence import (
    CandidateEvent,
    CandidateSetManifest,
    FrozenSnapshot,
    LineageBundle,
    RawCandidateSetCapture,
    assert_opportunity_set_parity,
    build_lineage_bundle,
    canonical_payload_sha256,
)
from .governance import (
    ProtocolAuthorizationBundle,
    verify_maturation_authorization,
)


OUTCOME_ENGINE_VERSION = "shadow-outcome-engine-v1"
BAR_SERIES_VERSION = "shadow-bar-series-v1"
CORPORATE_ACTION_POLICY_VERSION = "shadow-corporate-action-policy-v1"
OUTCOME_LEDGER_VERSION = "shadow-outcome-ledger-v1"
EXECUTION_POLICY_VERSION = "shadow-execution-policy-v1"

ACTIVATION_RULE = "FIRST_TRADING_SESSION_AFTER_SIGNAL"
HORIZON_CLOCK_RULE = "POST_FILL_SESSIONS_EXCLUDING_FILL_SESSION"
FILL_RULE = "BUY_LIMIT_OPEN_OR_INTRADAY_TOUCH_AT_ENTRY_HIGH"
GAP_RULE = "OBSERVED_OPEN_FOR_MARKETABLE_ENTRY_AND_GAP_EXITS"
ENTRY_GAP_THROUGH_STOP_RULE = (
    "FILL_AND_STOP_AT_OBSERVED_OPEN_USING_PLANNED_ENTRY_HIGH_RISK"
)
AMBIGUITY_RULE = "STOP_FIRST_AND_INTRADAY_ENTRY_TARGET_UNPROVEN"
CORPORATE_ACTION_RULE = (
    "RAW_AS_TRADED_BARS_WITH_FORWARD_SPLIT_AND_DIVIDEND_ADJUSTMENTS"
)
RIGHTS_TREATMENT_RULE = (
    "RIGHTS_INVALID_UNTIL_ELECTION_DELIVERY_AND_COST_RULES_ARE_FROZEN"
)
DIVIDEND_ENTITLEMENT_RULE = "POSITION_OPEN_BEFORE_EX_DATE"
UNFILLED_RULE = "EXPIRE_AFTER_ENTRY_VALIDITY_TRADING_DAYS"
LIQUIDITY_EXECUTION_RULE = "FIXED_ONE_LOT_NO_LIQUIDITY_MODEL"
PRICE_ROUNDING_RULE = "SOURCE_SUPPLIED_PRICES_NO_ENGINE_ROUNDING"
COST_MODEL_VERSION = "SHADOW_CASH_FLOW_BPS_V1"

Horizon = Literal[3, 5, 10, 15]
CorporateActionKind = Literal["DIVIDEND", "RIGHTS", "SPLIT"]
OutcomeSide = Literal["CONTROL", "CHALLENGER"]
BackfillStatus = Literal[
    "IGNORED_STALE",
    "INSERTED",
    "UNCHANGED",
    "UPDATED_PENDING",
    "UPDATED_TO_INVALID",
    "UPDATED_TO_MATURE",
]


class MaturationAuthorizationLoader(Protocol):
    """Reload current ledger-backed authorization at evaluation time.

    Operational callers must use a store-backed implementation that reads the
    append-only ledger on every call. Cached/static implementations are only
    suitable for isolated deterministic unit tests and grant no authority.
    """

    def load_authorization(
        self,
        *,
        protocol_id: str,
        manifest_canonical_sha256: str,
        ledger_id: str,
    ) -> ProtocolAuthorizationBundle: ...


class CorporateActionEvent(_StrictFrozenModel):
    """One point-in-time corporate-action source record."""

    event_id: str
    ticker: CanonicalTicker
    effective_date: date
    kind: CorporateActionKind
    price_factor: float = Field(gt=0.0)
    quantity_factor: float = Field(default=1.0, gt=0.0)
    capital_call_per_pre_event_share: float = Field(default=0.0, ge=0.0)
    cash_per_share: float = Field(ge=0.0)
    source_id: str
    source_definition_sha256: Sha256
    source_sha256: Sha256
    published_at: datetime
    terms_complete: Literal[True] = True

    @field_validator("published_at")
    @classmethod
    def require_aware_publication_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("corporate-action publication time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_action_shape(self) -> CorporateActionEvent:
        if self.kind in {"SPLIT", "RIGHTS"} and self.price_factor == 1.0:
            raise ValueError("split/rights event needs a non-unit price factor")
        if self.kind == "SPLIT":
            if self.capital_call_per_pre_event_share != 0.0:
                raise ValueError("split cannot require a capital contribution")
            if not math.isclose(
                self.price_factor * self.quantity_factor,
                1.0,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise ValueError("split price and quantity factors are inconsistent")
        if self.kind == "RIGHTS" and self.quantity_factor <= 1.0:
            raise ValueError("rights event needs a quantity factor above one")
        if self.kind == "DIVIDEND" and self.cash_per_share <= 0.0:
            raise ValueError("dividend event needs positive cash_per_share")
        if self.kind != "DIVIDEND" and self.cash_per_share != 0.0:
            raise ValueError("non-dividend event cannot carry cash dividend")
        if self.kind == "DIVIDEND" and (
            self.price_factor != 1.0
            or self.quantity_factor != 1.0
            or self.capital_call_per_pre_event_share != 0.0
        ):
            raise ValueError("dividend event cannot rescale price or quantity")
        expected = canonical_corporate_action_source_record_sha256(
            event_id=self.event_id,
            ticker=self.ticker,
            effective_date=self.effective_date,
            kind=self.kind,
            price_factor=self.price_factor,
            quantity_factor=self.quantity_factor,
            capital_call_per_pre_event_share=(
                self.capital_call_per_pre_event_share
            ),
            cash_per_share=self.cash_per_share,
            source_id=self.source_id,
            source_definition_sha256=self.source_definition_sha256,
            published_at=self.published_at,
        )
        if self.source_sha256 != expected:
            raise ValueError("corporate-action source-record hash mismatch")
        return self


class CorporateActionPolicy(_StrictFrozenModel):
    """Predeclared adjustment and dividend-return convention."""

    contract_version: Literal["shadow-corporate-action-policy-v1"] = (
        CORPORATE_ACTION_POLICY_VERSION
    )
    dividend_return_convention: Literal["PRICE_RETURN", "TOTAL_RETURN"]
    dividend_entitlement_rule: Literal["POSITION_OPEN_BEFORE_EX_DATE"]
    bar_price_basis: Literal["RAW_AS_TRADED"]
    prices_are_adjusted: Literal[False]
    dividends_are_in_prices: Literal[False]
    events: tuple[CorporateActionEvent, ...] = ()
    events_sha256: Sha256
    policy_sha256: Sha256

    @model_validator(mode="after")
    def verify_policy(self) -> CorporateActionPolicy:
        order = [(event.effective_date, event.event_id) for event in self.events]
        if order != sorted(order) or len(
            set(event.event_id for event in self.events)
        ) != len(self.events):
            raise ValueError("corporate-action events must be ordered and unique")
        quantity_dates = [
            event.effective_date
            for event in self.events
            if event.kind in {"SPLIT", "RIGHTS"}
        ]
        if len(quantity_dates) != len(set(quantity_dates)):
            raise ValueError(
                "multiple quantity-changing actions on one session are "
                "unsupported"
            )
        expected = _corporate_action_policy_hash(
            self.dividend_return_convention,
            self.dividend_entitlement_rule,
            self.bar_price_basis,
            self.prices_are_adjusted,
            self.dividends_are_in_prices,
        )
        if self.policy_sha256 != expected:
            raise ValueError("corporate-action policy hash mismatch")
        if self.events_sha256 != _corporate_action_events_hash(self.events):
            raise ValueError("corporate-action events hash mismatch")
        return self


class OutcomeBar(_StrictFrozenModel):
    """One canonical OHLC bar available at a known source vintage."""

    trade_date: date
    open: float = Field(gt=0.0)
    high: float = Field(gt=0.0)
    low: float = Field(gt=0.0)
    close: float = Field(gt=0.0)
    dividend_per_share: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="after")
    def verify_ohlc(self) -> OutcomeBar:
        if self.low > min(self.open, self.close):
            raise ValueError("bar low exceeds open/close")
        if self.high < max(self.open, self.close):
            raise ValueError("bar high is below open/close")
        for value in (self.open, self.high, self.low, self.close):
            if not math.isfinite(value):
                raise ValueError("bar prices must be finite")
        return self


class FrozenBarSeries(_StrictFrozenModel):
    """A complete, hash-checked bar source bounded by a point-in-time cutoff."""

    contract_version: Literal["shadow-bar-series-v1"] = BAR_SERIES_VERSION
    ticker: CanonicalTicker
    snapshot_id: str
    snapshot_sha256: Sha256
    source_id: str
    source_definition_sha256: Sha256
    source_sha256: Sha256
    source_as_of: datetime
    previous_source_sha256: Sha256 | None = None
    requested_start: date
    requested_end: date
    bars: tuple[OutcomeBar, ...]
    bars_sha256: Sha256
    bar_record_sha256s: tuple[Sha256, ...]
    corporate_action_policy: CorporateActionPolicy

    @field_validator("source_as_of")
    @classmethod
    def require_aware_source_time(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("bar-series source_as_of must be timezone-aware")
        return value

    @model_validator(mode="after")
    def verify_bar_series(self) -> FrozenBarSeries:
        if self.requested_end < self.requested_start:
            raise ValueError("bar-series requested range is inverted")
        dates = tuple(bar.trade_date for bar in self.bars)
        if len(set(dates)) != len(dates) or tuple(sorted(dates)) != dates:
            raise ValueError("bar-series dates must be unique and ordered")
        if any(
            bar.trade_date < self.requested_start
            or bar.trade_date > self.requested_end
            for bar in self.bars
        ):
            raise ValueError("bar-series contains a bar outside requested range")
        if self.bars and self.source_as_of < session_close_at(
            self.bars[-1].trade_date
        ):
            raise ValueError("bar source vintage precedes final session close")
        expected = _bars_hash(
            self.ticker,
            self.snapshot_id,
            self.snapshot_sha256,
            self.bars,
        )
        if self.bars_sha256 != expected:
            raise ValueError("bar-series hash mismatch")
        expected_records = tuple(
            canonical_outcome_bar_sha256(bar) for bar in self.bars
        )
        if self.bar_record_sha256s != expected_records:
            raise ValueError("bar-record hash sequence mismatch")
        if any(
            event.ticker != self.ticker
            for event in self.corporate_action_policy.events
        ):
            raise ValueError("corporate-action ticker differs from bar series")
        expected_source = canonical_outcome_source_record_sha256(
            source_id=self.source_id,
            source_definition_sha256=self.source_definition_sha256,
            source_as_of=self.source_as_of,
            requested_start=self.requested_start,
            requested_end=self.requested_end,
            ticker=self.ticker,
            snapshot_sha256=self.snapshot_sha256,
            bars_sha256=self.bars_sha256,
            corporate_action_policy_sha256=(
                self.corporate_action_policy.policy_sha256
            ),
            corporate_action_events_sha256=(
                self.corporate_action_policy.events_sha256
            ),
            previous_source_sha256=self.previous_source_sha256,
        )
        if self.source_sha256 != expected_source:
            raise ValueError("bar source-record hash mismatch")
        return self


class FrozenExecutionPolicy(_StrictFrozenModel):
    """Machine-readable implementation of one manifest's frozen label rules."""

    contract_version: Literal["shadow-execution-policy-v1"] = (
        EXECUTION_POLICY_VERSION
    )
    manifest_sha256: Sha256
    label_definition_sha256: Sha256
    cost_assumptions_sha256: Sha256
    trading_calendar_id: str
    trading_calendar_sha256: Sha256
    corporate_action_policy_sha256: Sha256
    fixed_terminal_date: date
    entry_validity_trading_days: int = Field(ge=1, le=15)
    activation_rule: Literal["FIRST_TRADING_SESSION_AFTER_SIGNAL"]
    horizon_clock_rule: Literal[
        "POST_FILL_SESSIONS_EXCLUDING_FILL_SESSION"
    ]
    fill_rule: Literal["BUY_LIMIT_OPEN_OR_INTRADAY_TOUCH_AT_ENTRY_HIGH"]
    gap_rule: Literal["OBSERVED_OPEN_FOR_MARKETABLE_ENTRY_AND_GAP_EXITS"]
    entry_gap_through_stop_rule: Literal[
        "FILL_AND_STOP_AT_OBSERVED_OPEN_USING_PLANNED_ENTRY_HIGH_RISK"
    ]
    same_bar_ambiguity_rule: Literal[
        "STOP_FIRST_AND_INTRADAY_ENTRY_TARGET_UNPROVEN"
    ]
    corporate_action_rule: Literal[
        "RAW_AS_TRADED_BARS_WITH_FORWARD_SPLIT_AND_DIVIDEND_ADJUSTMENTS"
    ]
    rights_treatment_rule: Literal[
        "RIGHTS_INVALID_UNTIL_ELECTION_DELIVERY_AND_COST_RULES_ARE_FROZEN"
    ]
    dividend_return_convention: Literal["PRICE_RETURN", "TOTAL_RETURN"]
    dividend_entitlement_rule: Literal["POSITION_OPEN_BEFORE_EX_DATE"]
    unfilled_rule: Literal["EXPIRE_AFTER_ENTRY_VALIDITY_TRADING_DAYS"]
    policy_sha256: Sha256

    @model_validator(mode="after")
    def verify_policy_hash(self) -> FrozenExecutionPolicy:
        expected = canonical_execution_policy_sha256(
            manifest_sha256=self.manifest_sha256,
            label_definition_sha256=self.label_definition_sha256,
            cost_assumptions_sha256=self.cost_assumptions_sha256,
            trading_calendar_id=self.trading_calendar_id,
            trading_calendar_sha256=self.trading_calendar_sha256,
            corporate_action_policy_sha256=(
                self.corporate_action_policy_sha256
            ),
            fixed_terminal_date=self.fixed_terminal_date,
            entry_validity_trading_days=self.entry_validity_trading_days,
            activation_rule=self.activation_rule,
            horizon_clock_rule=self.horizon_clock_rule,
            fill_rule=self.fill_rule,
            gap_rule=self.gap_rule,
            entry_gap_through_stop_rule=self.entry_gap_through_stop_rule,
            same_bar_ambiguity_rule=self.same_bar_ambiguity_rule,
            corporate_action_rule=self.corporate_action_rule,
            rights_treatment_rule=self.rights_treatment_rule,
            dividend_return_convention=self.dividend_return_convention,
            dividend_entitlement_rule=self.dividend_entitlement_rule,
            unfilled_rule=self.unfilled_rule,
        )
        if self.policy_sha256 != expected:
            raise ValueError("execution-policy hash mismatch")
        return self


class MaturationRequest(_StrictFrozenModel):
    """All frozen inputs required to evaluate one side and one horizon."""

    contract_version: Literal["shadow-outcome-engine-v1"] = OUTCOME_ENGINE_VERSION
    authorization: ProtocolAuthorizationBundle
    manifest: ShadowProtocolManifest
    raw_capture: RawCandidateSetCapture
    candidate_set: CandidateSetManifest
    candidate: CandidateEvent
    snapshot: FrozenSnapshot
    observation: ShadowObservation
    side: OutcomeSide
    decision: ShadowDecision
    horizon_trading_days: Horizon
    evaluation_cutoff: datetime
    execution_policy: FrozenExecutionPolicy
    trading_calendar: TradingCalendar
    bar_series: FrozenBarSeries

    @field_validator("evaluation_cutoff")
    @classmethod
    def require_aware_cutoff(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("evaluation cutoff must be timezone-aware")
        return value.astimezone(IDX_TIMEZONE)

    @model_validator(mode="after")
    def verify_request(self) -> MaturationRequest:
        verify_maturation_authorization(
            self.authorization,
            self.observation,
        )
        if canonical_sha256(self.authorization.manifest) != canonical_sha256(
            self.manifest
        ):
            raise ValueError("authorization manifest differs from request")
        if self.authorization.trading_calendar != self.trading_calendar:
            raise ValueError("authorization calendar differs from request")
        manifest_sha256 = _required_hash(self.manifest)
        _verify_protocol_component(
            self.observation.protocol_id,
            self.observation.component_id,
        )
        if (
            self.manifest.protocol_id,
            self.manifest.component_id,
            manifest_sha256,
        ) != (
            self.observation.protocol_id,
            self.observation.component_id,
            self.observation.manifest_sha256,
        ):
            raise ValueError("manifest does not match observation lineage")
        if (
            self.observation.signal_at
            < self.manifest.collection_start_not_before
        ):
            raise ValueError("signal predates approved collection window")
        if (
            self.manifest.universe.explicit_tickers
            and self.candidate.ticker
            not in self.manifest.universe.explicit_tickers
        ):
            raise ValueError("candidate ticker is outside the frozen universe")
        candidate_set_sha256 = _required_hash(self.candidate_set)
        if (
            self.observation.candidate_set_id
            != self.candidate_set.candidate_set_id
            or self.observation.candidate_set_sha256
            != candidate_set_sha256
        ):
            raise ValueError("observation is not bound to candidate set")
        member = next(
            (
                item
                for item in self.raw_capture.candidates
                if item.raw_event_id == self.candidate.raw_event_id
            ),
            None,
        )
        if member is None or canonical_sha256(member) != canonical_sha256(
            self.candidate
        ):
            raise ValueError("candidate content is absent from raw capture")
        if (
            self.candidate_set.raw_capture_id
            != self.raw_capture.raw_capture_id
            or self.candidate_set.raw_capture_sha256
            != _required_hash(self.raw_capture)
        ):
            raise ValueError("candidate set is not bound to raw capture")
        try:
            assert_opportunity_set_parity(
                self.candidate_set,
                self.raw_capture,
                self.candidate_set,
                self.raw_capture,
            )
        except ShadowContractError as exc:
            raise ValueError(
                "candidate set/raw capture identity mismatch"
            ) from exc
        if not (
            self.candidate.captured_at
            <= self.raw_capture.captured_at
            <= self.candidate_set.captured_at
            <= self.observation.captured_at
        ):
            raise ValueError("evidence capture chronology is not causal")
        candidate_identity = (
            self.candidate.protocol_id,
            self.candidate.component_id,
            self.candidate.manifest_sha256,
            self.candidate.raw_event_id,
            self.candidate.ticker,
            self.candidate.signal_at,
            self.candidate.as_of_date,
            self.candidate.snapshot_id,
            self.candidate.snapshot_sha256,
            self.candidate.opportunity_set_sha256,
        )
        observation_identity = (
            self.observation.protocol_id,
            self.observation.component_id,
            self.observation.manifest_sha256,
            self.observation.raw_event_id,
            self.observation.ticker,
            self.observation.signal_at,
            self.observation.as_of_date,
            self.observation.snapshot_id,
            self.observation.snapshot_sha256,
            self.observation.opportunity_set_sha256,
        )
        if candidate_identity != observation_identity:
            raise ValueError("candidate does not match observation")
        if (
            self.snapshot.snapshot_id != self.candidate.snapshot_id
            or self.snapshot.snapshot_sha256 != self.candidate.snapshot_sha256
            or self.snapshot.ticker != self.candidate.ticker
            or self.snapshot.as_of_date != self.candidate.as_of_date
            or self.snapshot.snapshot_as_of != self.candidate.snapshot_as_of
            or self.snapshot.source_record_sha256
            != self.candidate.snapshot_source_record_sha256
        ):
            raise ValueError("frozen snapshot does not match candidate")
        if self.snapshot.snapshot_as_of > self.observation.signal_at:
            raise ValueError("snapshot is newer than signal")
        if (
            self.snapshot.source_expires_at is not None
            and self.snapshot.source_expires_at <= self.observation.signal_at
        ):
            raise ValueError("snapshot source record expired before signal")
        if self.decision.decision_role != self.side:
            raise ValueError("maturation decision role does not match requested side")
        expected_decision = (
            self.observation.control_decision
            if self.side == "CONTROL"
            else self.observation.challenger_decision
        )
        if self.decision != expected_decision:
            raise ValueError("maturation decision is not the paired observation decision")
        if self.bar_series.ticker != self.observation.ticker:
            raise ValueError("bar-series ticker does not match observation")
        if self.bar_series.snapshot_id != self.observation.snapshot_id:
            raise ValueError("bar-series snapshot ID does not match observation")
        if self.bar_series.snapshot_sha256 != self.observation.snapshot_sha256:
            raise ValueError("bar-series snapshot hash does not match observation")
        if self.execution_policy != build_execution_policy(self.manifest):
            raise ValueError("execution policy differs from frozen manifest")
        if (
            self.trading_calendar.calendar_id
            != self.execution_policy.trading_calendar_id
            or self.trading_calendar.calendar_sha256
            != self.execution_policy.trading_calendar_sha256
        ):
            raise ValueError("trading calendar differs from execution policy")
        if (
            self.candidate.trading_calendar_id
            != self.trading_calendar.calendar_id
            or self.candidate.trading_calendar_sha256
            != self.trading_calendar.calendar_sha256
            or self.candidate_set.trading_calendar_sha256
            != self.trading_calendar.calendar_sha256
        ):
            raise ValueError("candidate calendar lineage mismatch")
        policy = self.bar_series.corporate_action_policy
        if (
            policy.policy_sha256
            != self.execution_policy.corporate_action_policy_sha256
            or policy.policy_sha256
            != self.candidate.corporate_action_policy_sha256
            or policy.policy_sha256
            != self.candidate_set.corporate_action_policy_sha256
        ):
            raise ValueError("corporate-action policy lineage mismatch")
        if (
            policy.dividend_return_convention
            != self.execution_policy.dividend_return_convention
            or policy.dividend_entitlement_rule
            != self.execution_policy.dividend_entitlement_rule
        ):
            raise ValueError("corporate-action policy semantics differ from labels")
        if (
            self.candidate.candidate_source_definition_sha256
            != self.manifest.universe.candidate_source_sha256
        ):
            raise ValueError("candidate source definition differs from manifest")
        manifest_source_hashes = {
            source.source_id: _required_hash(source)
            for source in self.manifest.sources
        }
        source_pairs = (
            (
                self.candidate.candidate_source_id,
                self.candidate.candidate_source_definition_sha256,
                "candidate",
            ),
            (
                self.snapshot.source_id,
                self.snapshot.source_definition_sha256,
                "snapshot",
            ),
            (
                self.bar_series.source_id,
                self.bar_series.source_definition_sha256,
                "outcome",
            ),
        )
        for source_id, source_hash, label in source_pairs:
            if manifest_source_hashes.get(source_id) != source_hash:
                raise ValueError(
                    f"{label} source ID/definition pair is absent from manifest"
                )
        if self.evaluation_cutoff < self.observation.captured_at:
            raise ValueError("evaluation cutoff precedes captured observation")
        if (
            self.execution_policy.fixed_terminal_date
            <= self.observation.signal_at.astimezone(IDX_TIMEZONE).date()
        ):
            raise ValueError("fixed terminal date must follow signal date")
        if (
            self.evaluation_cutoff
            > session_close_at(self.execution_policy.fixed_terminal_date)
        ):
            raise ValueError("evaluation cutoff cannot follow fixed terminal date")
        if self.bar_series.source_as_of > self.evaluation_cutoff:
            raise ValueError("bar source vintage is after evaluation cutoff")
        signal_local_date = self.observation.signal_at.astimezone(IDX_TIMEZONE).date()
        if (
            self.bar_series.requested_start != signal_local_date
            or self.bar_series.requested_end
            != self.execution_policy.fixed_terminal_date
        ):
            raise ValueError(
                "bar-series requested range differs from frozen outcome window"
            )
        if signal_local_date not in self.trading_calendar.sessions:
            raise ValueError("signal date is absent from frozen trading calendar")
        if (
            self.execution_policy.fixed_terminal_date
            not in self.trading_calendar.sessions
        ):
            raise ValueError(
                "fixed terminal date is absent from frozen trading calendar"
            )
        available_sessions = tuple(
            session
            for session in self.trading_calendar.sessions
            if (
                signal_local_date
                < session
                <= self.execution_policy.fixed_terminal_date
            )
        )
        validity = self.execution_policy.entry_validity_trading_days
        if len(available_sessions) < validity + 15:
            raise ValueError(
                "fixed terminal/calendar cannot support entry validity plus "
                "15 post-fill sessions"
            )
        for gate in self.decision.gate_measurements:
            if (
                manifest_source_hashes.get(gate.source_id)
                != gate.source_definition_sha256
            ):
                raise ValueError(
                    "gate source ID/definition pair is absent from manifest"
                )
            if gate.source_as_of is not None and gate.source_as_of > self.observation.signal_at:
                raise ValueError("gate source vintage is after signal")
            if (
                gate.expires_at is not None
                and gate.expires_at <= self.observation.signal_at
                and self.decision.would_be_actionable
            ):
                raise ValueError("actionable decision uses an expired gate source")
        for event in policy.events:
            if (
                manifest_source_hashes.get(event.source_id)
                != event.source_definition_sha256
            ):
                raise ValueError(
                    "corporate-action source ID/definition pair is absent "
                    "from manifest"
                )
            if event.published_at > self.evaluation_cutoff:
                raise ValueError("corporate action was published after cutoff")
            if event.published_at > self.bar_series.source_as_of:
                raise ValueError(
                    "corporate action was published after bar-source vintage"
                )
            if (
                signal_local_date
                < event.effective_date
                <= self.execution_policy.fixed_terminal_date
                and event.effective_date not in self.trading_calendar.sessions
            ):
                raise ValueError(
                    "corporate-action effective date is not a frozen "
                    "trading session"
                )
        signal_events = tuple(
            event
            for event in policy.events
            if event.published_at <= self.observation.signal_at
        )
        if (
            canonical_corporate_action_events_sha256(signal_events)
            != self.candidate.corporate_action_events_at_signal_sha256
        ):
            raise ValueError("signal-time corporate-action event lineage mismatch")
        calendar_dates = set(self.trading_calendar.sessions)
        if any(
            bar.trade_date not in calendar_dates
            for bar in self.bar_series.bars
        ):
            raise ValueError("bar date is absent from frozen trading calendar")
        for bar in self.bar_series.bars:
            expected_dividend = sum(
                event.cash_per_share
                for event in policy.events
                if (
                    event.kind == "DIVIDEND"
                    and event.effective_date == bar.trade_date
                )
            )
            if not math.isclose(
                bar.dividend_per_share,
                expected_dividend,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError("bar dividend differs from corporate-action events")
        if any(
            session_close_at(bar.trade_date) > self.evaluation_cutoff
            for bar in self.bar_series.bars
        ):
            raise ValueError("bar series contains future data for evaluation cutoff")
        return self


@dataclass(frozen=True)
class _FillEvidence:
    fill_price: float
    fill_date: date
    filled_at: datetime
    fill_time_precision: Literal["SESSION_OPEN", "SESSION_ONLY"]
    position_quantity: float
    invested_capital: float
    risk_capital_basis: float
    risk_fraction: float
    activation_bars_observed: int
    intraday_touch: bool
    entry_target_sequence_unproven: bool


def _reload_current_maturation_authorization(
    request: MaturationRequest,
    authorization_loader: MaturationAuthorizationLoader,
) -> MaturationRequest:
    """Replace any cached bundle with the ledger state loaded for this call."""

    trusted_request = MaturationRequest.model_validate(
        request.model_dump(mode="python")
    )
    manifest_sha256 = canonical_sha256(trusted_request.manifest)
    if manifest_sha256 is None:
        raise ShadowContractError("maturation manifest SHA-256 is unavailable")
    try:
        current_authorization = authorization_loader.load_authorization(
            protocol_id=trusted_request.manifest.protocol_id,
            manifest_canonical_sha256=manifest_sha256,
            ledger_id=trusted_request.authorization.approval_ledger.ledger_id,
        )
    except (AttributeError, OSError, ValueError) as exc:
        raise ShadowContractError(
            "current maturation authorization is unavailable"
        ) from exc
    try:
        return MaturationRequest.model_validate(
            {
                **trusted_request.model_dump(mode="python"),
                "authorization": current_authorization,
            }
        )
    except ValueError as exc:
        raise ShadowContractError(
            "current maturation authorization rejected the request"
        ) from exc


def evaluate_horizon(
    request: MaturationRequest,
    *,
    authorization_loader: MaturationAuthorizationLoader,
) -> ShadowOutcome:
    """Evaluate one horizon after reloading the current approval ledger.

    The reload is the evaluation linearization point. Any persisted outcome
    must pass the same current-ledger check again through replay/backfill, so a
    closure that arrives during computation is fail-closed before persistence.
    """

    request = _reload_current_maturation_authorization(
        request,
        authorization_loader,
    )
    original_geometry = request.decision.geometry
    if original_geometry is None or not request.decision.would_be_actionable:
        return _invalid_outcome(
            request,
            reason="NO_ACTIONABLE_GEOMETRY",
            bars_observed=0,
        )

    bars_by_date = {bar.trade_date: bar for bar in request.bar_series.bars}
    activation_sessions = _activation_sessions(request)
    closed_activation = tuple(
        session
        for session in activation_sessions
        if session_close_at(session) <= request.evaluation_cutoff
    )
    fill: _FillEvidence | None = None
    for index, session in enumerate(closed_activation, start=1):
        if session not in bars_by_date:
            return _invalid_outcome(
                request,
                reason="MISSING_REQUIRED_SESSION_BAR",
                bars_observed=index - 1,
            )
        if _rights_effective_on(request, session):
            return _invalid_outcome(
                request,
                reason="RIGHTS_POLICY_UNSUPPORTED",
                bars_observed=index - 1,
            )
        bar = bars_by_date[session]
        geometry = _geometry_for_date(
            original_geometry,
            request,
            session,
        )
        entry = _entry_fill_price(bar, geometry)
        if entry is None:
            continue
        fill_price, intraday_touch = entry
        position_quantity = float(request.manifest.costs.lot_size)
        invested_capital = fill_price * position_quantity
        risk_per_share = (
            geometry.entry_high - geometry.stop_loss
            if fill_price <= geometry.stop_loss
            else fill_price - geometry.stop_loss
        )
        risk_capital_basis = risk_per_share * position_quantity
        fill = _FillEvidence(
            fill_price=fill_price,
            fill_date=session,
            filled_at=(
                session_close_at(session)
                if intraday_touch
                else _bar_open_time(session)
            ),
            fill_time_precision=(
                "SESSION_ONLY" if intraday_touch else "SESSION_OPEN"
            ),
            position_quantity=position_quantity,
            invested_capital=invested_capital,
            risk_capital_basis=risk_capital_basis,
            risk_fraction=risk_capital_basis / invested_capital,
            activation_bars_observed=index,
            intraday_touch=intraday_touch,
            entry_target_sequence_unproven=(
                intraday_touch and bar.high >= geometry.target_price
            ),
        )
        terminal = _terminal_for_bar(
            bar,
            geometry,
            intraday_fill=intraday_touch,
        )
        if terminal is not None:
            event, exit_price, ambiguous, reason = terminal
            if fill_price <= geometry.stop_loss:
                reason = "ENTRY_GAP_THROUGH_STOP_GAP"
            return _realized_outcome(
                request,
                fill=fill,
                exit_price=exit_price,
                exit_value=exit_price * position_quantity,
                invested_capital=invested_capital,
                dividend_cash=0.0,
                closed_at=_terminal_time(session, reason),
                maturity_at=_terminal_time(session, reason),
                terminal_event=event,
                same_bar_ambiguous=ambiguous,
                bars_observed=index,
                reason=reason,
            )
        break

    if fill is None:
        if len(closed_activation) < len(activation_sessions):
            return _pending_outcome(
                request,
                fill=None,
                bars_observed=len(closed_activation),
            )
        return _mature_unfilled(
            request,
            maturity_at=session_close_at(activation_sessions[-1]),
            bars_observed=len(activation_sessions),
        )

    horizon_sessions = tuple(
        session
        for session in _eligible_sessions(request)
        if session > fill.fill_date
    )[: request.horizon_trading_days]
    closed_horizon = tuple(
        session
        for session in horizon_sessions
        if session_close_at(session) <= request.evaluation_cutoff
    )
    shares = fill.position_quantity
    invested_capital = fill.invested_capital
    dividend_cash = 0.0
    for offset, session in enumerate(closed_horizon, start=1):
        if session not in bars_by_date:
            return _invalid_outcome(
                request,
                reason="MISSING_REQUIRED_SESSION_BAR",
                bars_observed=fill.activation_bars_observed + offset - 1,
                fill=fill,
            )
        if _rights_effective_on(request, session):
            return _invalid_outcome(
                request,
                reason="RIGHTS_POLICY_UNSUPPORTED",
                bars_observed=fill.activation_bars_observed + offset - 1,
                fill=fill,
            )
        bar = bars_by_date[session]
        shares, invested_capital = _apply_position_actions(
            request,
            session,
            shares,
            invested_capital,
        )
        dividend_cash += _dividend_cash_for_bar(
            request,
            bar,
            shares,
            fill_date=fill.fill_date,
        )
        geometry = _geometry_for_date(
            original_geometry,
            request,
            session,
        )
        terminal = _terminal_for_bar(
            bar,
            geometry,
            intraday_fill=False,
        )
        bars_observed = fill.activation_bars_observed + offset
        if terminal is not None:
            event, exit_price, ambiguous, reason = terminal
            return _realized_outcome(
                request,
                fill=fill,
                exit_price=exit_price,
                exit_value=exit_price * shares,
                invested_capital=invested_capital,
                dividend_cash=dividend_cash,
                closed_at=_terminal_time(session, reason),
                maturity_at=_terminal_time(session, reason),
                terminal_event=event,
                same_bar_ambiguous=ambiguous,
                bars_observed=bars_observed,
                reason=reason,
            )

    if len(closed_horizon) < request.horizon_trading_days:
        return _pending_outcome(
            request,
            fill=fill,
            bars_observed=(
                fill.activation_bars_observed + len(closed_horizon)
            ),
        )

    final_bar = bars_by_date[horizon_sessions[-1]]
    return _realized_outcome(
        request,
        fill=fill,
        exit_price=final_bar.close,
        exit_value=final_bar.close * shares,
        invested_capital=invested_capital,
        dividend_cash=dividend_cash,
        closed_at=session_close_at(final_bar.trade_date),
        maturity_at=session_close_at(final_bar.trade_date),
        terminal_event="TIMEOUT",
        same_bar_ambiguous=False,
        bars_observed=(
            fill.activation_bars_observed + len(closed_horizon)
        ),
        reason="TIMEOUT_HORIZON",
    )


def evaluate_all_horizons(
    request: MaturationRequest,
    *,
    authorization_loader: MaturationAuthorizationLoader,
) -> tuple[ShadowOutcome, ...]:
    """Return independent 3/5/10/15-session outcomes in declared order."""

    return tuple(
        evaluate_horizon(
            MaturationRequest.model_validate(
                {
                    **request.model_dump(mode="python"),
                    "horizon_trading_days": horizon,
                }
            ),
            authorization_loader=authorization_loader,
        )
        for horizon in (3, 5, 10, 15)
    )


def verify_outcome_against_request(
    request: MaturationRequest,
    outcome: ShadowOutcome,
    *,
    authorization_loader: MaturationAuthorizationLoader,
) -> ShadowOutcome:
    """Replay frozen inputs and reject any non-identical outcome artifact."""

    trusted_request = MaturationRequest.model_validate(
        request.model_dump(mode="python")
    )
    trusted_outcome = ShadowOutcome.model_validate(
        outcome.model_dump(mode="python")
    )
    if (
        trusted_outcome.horizon_trading_days
        != trusted_request.horizon_trading_days
    ):
        raise ShadowContractError("outcome horizon differs from replay request")
    expected = evaluate_horizon(
        trusted_request,
        authorization_loader=authorization_loader,
    )
    if canonical_sha256(expected) != canonical_sha256(trusted_outcome):
        raise ShadowContractError(
            "outcome differs from deterministic frozen-input replay"
        )
    return trusted_outcome


def build_verified_outcome_lineage(
    request: MaturationRequest,
    outcome: ShadowOutcome,
    *,
    authorization_loader: MaturationAuthorizationLoader,
) -> LineageBundle:
    """Replay an outcome, then construct its full structural hash lineage."""

    trusted = verify_outcome_against_request(
        request,
        outcome,
        authorization_loader=authorization_loader,
    )
    return build_lineage_bundle(
        request.manifest,
        request.snapshot,
        request.raw_capture,
        request.candidate_set,
        request.candidate,
        request.observation,
        bar_series=request.bar_series,
        outcome=trusted,
    )


class OutcomeLedger(_StrictFrozenModel):
    """Immutable latest-state view keyed by observation and horizon."""

    contract_version: Literal["shadow-outcome-ledger-v1"] = OUTCOME_LEDGER_VERSION
    records: tuple[ShadowOutcome, ...] = ()

    @model_validator(mode="after")
    def verify_records(self) -> OutcomeLedger:
        keys: set[tuple[str, str, str, str, int]] = set()
        raw_keys: set[tuple[str, str, str, str, int]] = set()
        outcome_ids: set[str] = set()
        ordered_keys: list[tuple[str, str, str, str, int]] = []
        for record in self.records:
            key = _outcome_key(record)
            if key in keys:
                raise ValueError("outcome ledger contains duplicate identity")
            keys.add(key)
            raw_key = (
                record.protocol_id,
                record.manifest_sha256,
                record.raw_event_id,
                record.decision_role,
                record.horizon_trading_days,
            )
            if raw_key in raw_keys:
                raise ValueError("outcome ledger duplicates a raw-event maturity")
            raw_keys.add(raw_key)
            if record.outcome_id in outcome_ids:
                raise ValueError("outcome ledger contains duplicate outcome ID")
            outcome_ids.add(record.outcome_id)
            ordered_keys.append(key)
        if ordered_keys != sorted(ordered_keys):
            raise ValueError("outcome ledger records must use canonical order")
        return self

    def backfill(
        self,
        incoming: ShadowOutcome,
        *,
        request: MaturationRequest,
        authorization_loader: MaturationAuthorizationLoader,
    ) -> tuple[OutcomeLedger, BackfillStatus]:
        """Recheck current A1 state, then merge one result monotonically."""

        incoming = verify_outcome_against_request(
            request,
            incoming,
            authorization_loader=authorization_loader,
        )
        key = _outcome_key(incoming)
        for index, existing in enumerate(self.records):
            if _outcome_key(existing) != key:
                continue
            if canonical_sha256(existing) == canonical_sha256(incoming):
                return self, "UNCHANGED"
            if _immutable_outcome_identity(existing) != _immutable_outcome_identity(
                incoming
            ):
                raise ShadowContractError("outcome immutable lineage changed")
            if existing.status in {"MATURE", "INVALID"}:
                raise ShadowContractError(
                    "terminal outcome cannot be overwritten"
                )
            if incoming.evaluated_at <= existing.evaluated_at:
                raise ShadowContractError("outcome backfill cutoff moved backward")
            if incoming.outcome_source_as_of < existing.outcome_source_as_of:
                raise ShadowContractError("outcome source vintage moved backward")
            if incoming.bars_observed < existing.bars_observed:
                raise ShadowContractError("outcome backfill lost observed sessions")
            if existing.outcome_bar_record_sha256s != (
                incoming.outcome_bar_record_sha256s[
                    : len(existing.outcome_bar_record_sha256s)
                ]
            ):
                raise ShadowContractError("historical bar prefix changed")
            existing_events = dict(
                zip(
                    existing.corporate_action_event_ids,
                    existing.corporate_action_event_record_sha256s,
                    strict=True,
                )
            )
            incoming_events = dict(
                zip(
                    incoming.corporate_action_event_ids,
                    incoming.corporate_action_event_record_sha256s,
                    strict=True,
                )
            )
            if any(
                incoming_events.get(event_id) != event_hash
                for event_id, event_hash in existing_events.items()
            ):
                raise ShadowContractError(
                    "historical corporate-action evidence changed or disappeared"
                )
            incoming_publications = dict(
                zip(
                    incoming.corporate_action_event_ids,
                    incoming.corporate_action_event_published_ats,
                    strict=True,
                )
            )
            if any(
                incoming_publications[event_id] <= existing.evaluated_at
                for event_id in incoming_events.keys() - existing_events.keys()
            ):
                raise ShadowContractError(
                    "corporate-action evidence was retroactively added to "
                    "an earlier evaluation vintage"
                )
            if (
                (
                    existing.outcome_bars_sha256
                    != incoming.outcome_bars_sha256
                    or existing.corporate_action_events_sha256
                    != incoming.corporate_action_events_sha256
                )
                and existing.outcome_source_sha256
                == incoming.outcome_source_sha256
            ):
                raise ShadowContractError(
                    "source content changed without a new vintage hash"
                )
            if (
                incoming.outcome_source_sha256
                != existing.outcome_source_sha256
                and incoming.previous_outcome_source_sha256
                != existing.outcome_source_sha256
            ):
                raise ShadowContractError("outcome source-vintage chain is broken")
            if existing.fill_status == "FILLED":
                stable_fill = (
                    existing.fill_status,
                    existing.fill_price,
                    existing.filled_at,
                    existing.fill_time_precision,
                )
                incoming_fill = (
                    incoming.fill_status,
                    incoming.fill_price,
                    incoming.filled_at,
                    incoming.fill_time_precision,
                )
                if stable_fill != incoming_fill:
                    raise ShadowContractError("recorded fill evidence changed")
            updated = list(self.records)
            updated[index] = incoming
            status: BackfillStatus
            if incoming.status == "MATURE":
                status = "UPDATED_TO_MATURE"
            elif incoming.status == "INVALID":
                status = "UPDATED_TO_INVALID"
            else:
                status = "UPDATED_PENDING"
            return OutcomeLedger(records=tuple(updated)), status
        inserted = tuple(
            sorted((*self.records, incoming), key=_outcome_key)
        )
        return OutcomeLedger(records=inserted), "INSERTED"

    @property
    def ledger_sha256(self) -> str:
        digest = canonical_sha256(self)
        if digest is None:  # pragma: no cover
            raise ShadowContractError("cannot hash outcome ledger")
        return digest


def _entry_fill_price(
    bar: OutcomeBar,
    geometry: RecordedTradeGeometry,
) -> tuple[float, bool] | None:
    limit_price = geometry.entry_high
    if bar.open <= limit_price:
        return bar.open, False
    if bar.low <= limit_price:
        return limit_price, True
    return None


def _terminal_for_bar(
    bar: OutcomeBar,
    geometry: RecordedTradeGeometry,
    *,
    intraday_fill: bool,
) -> tuple[Literal["STOP_FIRST", "TARGET_FIRST"], float, bool, str] | None:
    stop_hit = bar.low <= geometry.stop_loss
    target_hit = bar.high >= geometry.target_price
    if not intraday_fill and bar.open <= geometry.stop_loss:
        return "STOP_FIRST", bar.open, False, "STOP_GAP"
    if not intraday_fill and bar.open >= geometry.target_price:
        return "TARGET_FIRST", bar.open, False, "TARGET_GAP"
    if intraday_fill and stop_hit:
        return (
            "STOP_FIRST",
            geometry.stop_loss,
            True,
            "ENTRY_TOUCH_STOP_ORDER_UNKNOWN",
        )
    if intraday_fill and target_hit:
        return None
    if stop_hit and target_hit:
        return "STOP_FIRST", geometry.stop_loss, True, "SAME_BAR_STOP_FIRST"
    if stop_hit:
        return "STOP_FIRST", geometry.stop_loss, False, "STOP_TOUCH"
    if target_hit:
        return "TARGET_FIRST", geometry.target_price, False, "TARGET_TOUCH"
    return None


def _realized_outcome(
    request: MaturationRequest,
    *,
    fill: _FillEvidence,
    exit_price: float,
    exit_value: float,
    invested_capital: float,
    dividend_cash: float,
    closed_at: datetime,
    maturity_at: datetime,
    terminal_event: Literal["STOP_FIRST", "TARGET_FIRST", "TIMEOUT"],
    same_bar_ambiguous: bool,
    bars_observed: int,
    reason: str,
) -> ShadowOutcome:
    entry_cost_cash = _entry_cost_cash(
        fill.invested_capital,
        request.manifest.costs,
    )
    exit_cost_cash = _exit_cost_cash(
        exit_value,
        request.manifest.costs,
    )
    total_cost_cash = entry_cost_cash + exit_cost_cash
    total_cost_fraction = total_cost_cash / invested_capital
    capital_return = (exit_value - invested_capital) / invested_capital
    dividend_return = dividend_cash / invested_capital
    gross_return = capital_return + dividend_return
    net_return = gross_return - total_cost_fraction
    net_r = net_return / fill.risk_fraction
    ambiguous = (
        same_bar_ambiguous or fill.entry_target_sequence_unproven
    )
    if same_bar_ambiguous:
        ambiguity_resolution = "STOP_FIRST"
    elif fill.entry_target_sequence_unproven:
        ambiguity_resolution = "INTRADAY_ENTRY_TARGET_UNPROVEN_NOT_CREDITED"
    else:
        ambiguity_resolution = None
    reason_codes = (reason,)
    if (
        fill.entry_target_sequence_unproven
        and "INTRADAY_ENTRY_TARGET_UNPROVEN" not in reason_codes
    ):
        reason_codes = (*reason_codes, "INTRADAY_ENTRY_TARGET_UNPROVEN")
    return ShadowOutcome(
        **_outcome_common(request),
        horizon_trading_days=request.horizon_trading_days,
        primary_horizon=request.horizon_trading_days == 15,
        status="MATURE",
        fill_status="FILLED",
        terminal_event=terminal_event,
        signal_at=request.observation.signal_at,
        evaluated_at=request.evaluation_cutoff,
        maturity_at=maturity_at,
        filled_at=fill.filled_at,
        closed_at=closed_at,
        fill_time_precision=fill.fill_time_precision,
        planned_geometry_sha256=_required_hash(request.decision.geometry),
        bars_observed=bars_observed,
        fill_price=fill.fill_price,
        exit_price=exit_price,
        position_quantity_at_exit=exit_value / exit_price,
        invested_capital=invested_capital,
        exit_position_value=exit_value,
        dividend_cash=dividend_cash,
        entry_cost_cash=entry_cost_cash,
        exit_cost_cash=exit_cost_cash,
        total_cost_cash=total_cost_cash,
        risk_capital_basis=fill.risk_capital_basis,
        capital_return=capital_return,
        dividend_return=dividend_return,
        gross_return=gross_return,
        net_return=net_return,
        net_r=net_r,
        risk_fraction_at_fill=fill.risk_fraction,
        total_cost_fraction=total_cost_fraction,
        same_bar_ambiguous=ambiguous,
        ambiguity_resolution=ambiguity_resolution,
        corporate_action_adjustment=_outcome_geometry_adjustment(request),
        reason_codes=reason_codes,
    )


def _pending_outcome(
    request: MaturationRequest,
    *,
    fill: _FillEvidence | None,
    bars_observed: int,
) -> ShadowOutcome:
    return ShadowOutcome(
        **_outcome_common(request),
        horizon_trading_days=request.horizon_trading_days,
        primary_horizon=request.horizon_trading_days == 15,
        status="PENDING",
        fill_status="FILLED" if fill is not None else "PENDING",
        terminal_event="PENDING",
        signal_at=request.observation.signal_at,
        evaluated_at=request.evaluation_cutoff,
        planned_geometry_sha256=_required_hash(request.decision.geometry),
        bars_observed=bars_observed,
        fill_price=fill.fill_price if fill is not None else None,
        filled_at=fill.filled_at if fill is not None else None,
        fill_time_precision=(
            fill.fill_time_precision if fill is not None else None
        ),
        same_bar_ambiguous=(
            fill.entry_target_sequence_unproven
            if fill is not None
            else False
        ),
        ambiguity_resolution=(
            "INTRADAY_ENTRY_TARGET_UNPROVEN_NOT_CREDITED"
            if fill is not None and fill.entry_target_sequence_unproven
            else None
        ),
        corporate_action_adjustment=_outcome_geometry_adjustment(request),
        reason_codes=(
            ("PENDING_MATURITY", "INTRADAY_ENTRY_TARGET_UNPROVEN")
            if fill is not None and fill.entry_target_sequence_unproven
            else ("PENDING_MATURITY",)
        ),
    )


def _mature_unfilled(
    request: MaturationRequest,
    *,
    maturity_at: datetime,
    bars_observed: int,
) -> ShadowOutcome:
    return ShadowOutcome(
        **_outcome_common(request),
        horizon_trading_days=request.horizon_trading_days,
        primary_horizon=request.horizon_trading_days == 15,
        status="MATURE",
        fill_status="EXPIRED_UNFILLED",
        terminal_event="UNFILLED",
        signal_at=request.observation.signal_at,
        evaluated_at=request.evaluation_cutoff,
        maturity_at=maturity_at,
        planned_geometry_sha256=_required_hash(request.decision.geometry),
        bars_observed=bars_observed,
        corporate_action_adjustment=_outcome_geometry_adjustment(request),
        reason_codes=("UNFILLED_EXPIRED",),
    )


def _invalid_outcome(
    request: MaturationRequest,
    *,
    reason: str,
    bars_observed: int,
    fill: _FillEvidence | None = None,
) -> ShadowOutcome:
    geometry_hash = canonical_sha256(request.decision.geometry)
    if geometry_hash is None:
        geometry_hash = "0" * 64
    return ShadowOutcome(
        **_outcome_common(request),
        horizon_trading_days=request.horizon_trading_days,
        primary_horizon=request.horizon_trading_days == 15,
        status="INVALID",
        fill_status="FILLED" if fill is not None else "INVALID",
        terminal_event="INVALID",
        signal_at=request.observation.signal_at,
        evaluated_at=request.evaluation_cutoff,
        planned_geometry_sha256=geometry_hash,
        bars_observed=bars_observed,
        fill_price=fill.fill_price if fill is not None else None,
        filled_at=fill.filled_at if fill is not None else None,
        fill_time_precision=(
            fill.fill_time_precision if fill is not None else None
        ),
        same_bar_ambiguous=(
            fill.entry_target_sequence_unproven
            if fill is not None
            else False
        ),
        ambiguity_resolution=(
            "INTRADAY_ENTRY_TARGET_UNPROVEN_NOT_CREDITED"
            if fill is not None and fill.entry_target_sequence_unproven
            else None
        ),
        corporate_action_adjustment=_outcome_geometry_adjustment(request),
        reason_codes=(
            (reason, "INTRADAY_ENTRY_TARGET_UNPROVEN")
            if fill is not None and fill.entry_target_sequence_unproven
            else (reason,)
        ),
    )


def _outcome_common(request: MaturationRequest) -> dict[str, object]:
    observation = request.observation
    policy = request.bar_series.corporate_action_policy
    return {
        "protocol_id": observation.protocol_id,
        "component_id": observation.component_id,
        "decision_role": request.side,
        "manifest_sha256": observation.manifest_sha256,
        "candidate_set_sha256": observation.candidate_set_sha256,
        "outcome_id": _outcome_id(request),
        "observation_id": observation.observation_id,
        "raw_event_id": observation.raw_event_id,
        "independent_cluster_id": observation.independent_cluster_id,
        "ticker": observation.ticker,
        "snapshot_id": observation.snapshot_id,
        "snapshot_sha256": observation.snapshot_sha256,
        "trading_calendar_sha256": (
            request.trading_calendar.calendar_sha256
        ),
        "label_definition_sha256": _required_hash(request.manifest.labels),
        "cost_assumptions_sha256": _required_hash(request.manifest.costs),
        "execution_policy_sha256": request.execution_policy.policy_sha256,
        "outcome_source_id": request.bar_series.source_id,
        "outcome_source_definition_sha256": (
            request.bar_series.source_definition_sha256
        ),
        "outcome_source_sha256": request.bar_series.source_sha256,
        "previous_outcome_source_sha256": (
            request.bar_series.previous_source_sha256
        ),
        "outcome_source_as_of": request.bar_series.source_as_of,
        "outcome_bars_sha256": request.bar_series.bars_sha256,
        "outcome_bar_record_sha256s": (
            request.bar_series.bar_record_sha256s
        ),
        "corporate_action_policy_sha256": policy.policy_sha256,
        "corporate_action_events_sha256": policy.events_sha256,
        "corporate_action_event_ids": tuple(
            event.event_id for event in policy.events
        ),
        "corporate_action_event_record_sha256s": tuple(
            event.source_sha256 for event in policy.events
        ),
        "corporate_action_event_published_ats": tuple(
            event.published_at for event in policy.events
        ),
    }


def _outcome_geometry_adjustment(
    request: MaturationRequest,
) -> str | None:
    signal_date = request.observation.signal_at.astimezone(
        IDX_TIMEZONE
    ).date()
    events = tuple(
        event
        for event in request.bar_series.corporate_action_policy.events
        if (
            event.kind == "SPLIT"
            and event.effective_date > signal_date
            and event.effective_date <= request.evaluation_cutoff.date()
        )
    )
    if not events:
        return None
    return canonical_geometry_adjustment_sha256(
        request.decision.geometry,
        events,
    )


def _activation_sessions(request: MaturationRequest) -> tuple[date, ...]:
    return _eligible_sessions(request)[
        : request.execution_policy.entry_validity_trading_days
    ]


def _geometry_for_date(
    geometry: RecordedTradeGeometry,
    request: MaturationRequest,
    day: date,
) -> RecordedTradeGeometry:
    signal_date = request.observation.signal_at.astimezone(
        IDX_TIMEZONE
    ).date()
    factor = math.prod(
        event.price_factor
        for event in request.bar_series.corporate_action_policy.events
        if (
            event.kind == "SPLIT"
            and signal_date < event.effective_date <= day
        )
    )
    return RecordedTradeGeometry(
        entry_low=geometry.entry_low * factor,
        entry_high=geometry.entry_high * factor,
        target_price=geometry.target_price * factor,
        stop_loss=geometry.stop_loss * factor,
        risk_reward_ratio=geometry.risk_reward_ratio,
        required_risk_reward=geometry.required_risk_reward,
    )


def _apply_position_actions(
    request: MaturationRequest,
    day: date,
    shares: float,
    invested_capital: float,
) -> tuple[float, float]:
    for event in request.bar_series.corporate_action_policy.events:
        if (
            event.effective_date != day
            or event.kind != "SPLIT"
        ):
            continue
        prior_shares = shares
        invested_capital += (
            event.capital_call_per_pre_event_share * prior_shares
        )
        shares *= event.quantity_factor
    return shares, invested_capital


def _rights_effective_on(request: MaturationRequest, day: date) -> bool:
    return any(
        event.kind == "RIGHTS" and event.effective_date == day
        for event in request.bar_series.corporate_action_policy.events
    )


def _dividend_cash_for_bar(
    request: MaturationRequest,
    bar: OutcomeBar,
    shares: float,
    *,
    fill_date: date,
) -> float:
    policy = request.bar_series.corporate_action_policy
    if (
        policy.dividend_return_convention != "TOTAL_RETURN"
        or policy.dividends_are_in_prices
        or fill_date >= bar.trade_date
    ):
        return 0.0
    return bar.dividend_per_share * shares


def _terminal_time(day: date, reason: str) -> datetime:
    return _bar_open_time(day) if reason.endswith("_GAP") else session_close_at(day)


def _eligible_sessions(request: MaturationRequest) -> tuple[date, ...]:
    return tuple(
        session
        for session in request.trading_calendar.sessions
        if (
            request.observation.signal_at.astimezone(IDX_TIMEZONE).date()
            < session
            <= request.execution_policy.fixed_terminal_date
        )
    )


def _outcome_id(request: MaturationRequest) -> str:
    return canonical_outcome_id(
        protocol_id=request.observation.protocol_id,
        manifest_sha256=request.observation.manifest_sha256,
        observation_id=request.observation.observation_id,
        raw_event_id=request.observation.raw_event_id,
        ticker=request.observation.ticker,
        signal_at=request.observation.signal_at,
        decision_role=request.side,
        horizon_trading_days=request.horizon_trading_days,
    )


def _outcome_key(record: ShadowOutcome) -> tuple[str, str, str, str, int]:
    return (
        record.protocol_id,
        record.manifest_sha256,
        record.observation_id,
        record.decision_role,
        record.horizon_trading_days,
    )


def _immutable_outcome_identity(record: ShadowOutcome) -> tuple[object, ...]:
    return (
        record.protocol_id,
        record.component_id,
        record.manifest_sha256,
        record.candidate_set_sha256,
        record.outcome_id,
        record.observation_id,
        record.raw_event_id,
        record.independent_cluster_id,
        record.ticker,
        record.signal_at,
        record.snapshot_id,
        record.snapshot_sha256,
        record.decision_role,
        record.horizon_trading_days,
        record.primary_horizon,
        record.planned_geometry_sha256,
        record.trading_calendar_sha256,
        record.label_definition_sha256,
        record.cost_assumptions_sha256,
        record.execution_policy_sha256,
        record.outcome_source_id,
        record.outcome_source_definition_sha256,
        record.corporate_action_policy_sha256,
        record.corporate_action_adjustment,
    )


def _bar_open_time(day: date) -> datetime:
    return datetime.combine(day, SESSION_OPEN, tzinfo=IDX_TIMEZONE)


def _entry_cost_cash(
    entry_notional: float,
    costs: CostAssumptions,
) -> float:
    rate = (
        costs.buy_commission_bps
        + costs.slippage_bps
        + costs.bid_ask_bps
    ) / 10_000.0
    return entry_notional * rate


def _exit_cost_cash(
    exit_notional: float,
    costs: CostAssumptions,
) -> float:
    rate = (
        costs.sell_commission_bps
        + costs.sell_tax_bps
        + costs.slippage_bps
        + costs.bid_ask_bps
    ) / 10_000.0
    return exit_notional * rate


def build_execution_policy(
    manifest: ShadowProtocolManifest,
) -> FrozenExecutionPolicy:
    """Translate one manifest into the only execution semantics this engine supports."""

    labels: LabelDefinition = manifest.labels
    supported = {
        "activation_rule": ACTIVATION_RULE,
        "horizon_clock_rule": HORIZON_CLOCK_RULE,
        "fill_rule": FILL_RULE,
        "gap_rule": GAP_RULE,
        "entry_gap_through_stop_rule": ENTRY_GAP_THROUGH_STOP_RULE,
        "same_bar_ambiguity_rule": AMBIGUITY_RULE,
        "corporate_action_rule": CORPORATE_ACTION_RULE,
        "rights_treatment_rule": RIGHTS_TREATMENT_RULE,
        "dividend_entitlement_rule": DIVIDEND_ENTITLEMENT_RULE,
        "unfilled_rule": UNFILLED_RULE,
    }
    for field, expected in supported.items():
        if getattr(labels, field) != expected:
            raise ShadowContractError(
                f"unsupported frozen label rule: {field}"
            )
    if (
        manifest.costs.liquidity_execution_rule != LIQUIDITY_EXECUTION_RULE
        or manifest.costs.price_rounding_rule != PRICE_ROUNDING_RULE
        or manifest.costs.cost_model_version != COST_MODEL_VERSION
    ):
        raise ShadowContractError(
            "unsupported frozen cost/liquidity/rounding convention"
        )
    manifest_sha256 = _required_hash(manifest)
    label_sha256 = _required_hash(labels)
    cost_sha256 = _required_hash(manifest.costs)
    values = {
        "manifest_sha256": manifest_sha256,
        "label_definition_sha256": label_sha256,
        "cost_assumptions_sha256": cost_sha256,
        "trading_calendar_id": manifest.trading_calendar_id,
        "trading_calendar_sha256": manifest.trading_calendar_sha256,
        "corporate_action_policy_sha256": (
            manifest.corporate_action_policy_sha256
        ),
        "fixed_terminal_date": manifest.fixed_terminal_date,
        "entry_validity_trading_days": (
            labels.entry_validity_trading_days
        ),
        "activation_rule": labels.activation_rule,
        "horizon_clock_rule": labels.horizon_clock_rule,
        "fill_rule": labels.fill_rule,
        "gap_rule": labels.gap_rule,
        "entry_gap_through_stop_rule": labels.entry_gap_through_stop_rule,
        "same_bar_ambiguity_rule": labels.same_bar_ambiguity_rule,
        "corporate_action_rule": labels.corporate_action_rule,
        "rights_treatment_rule": labels.rights_treatment_rule,
        "dividend_return_convention": (
            labels.dividend_return_convention
        ),
        "dividend_entitlement_rule": labels.dividend_entitlement_rule,
        "unfilled_rule": labels.unfilled_rule,
    }
    return FrozenExecutionPolicy(
        **values,
        policy_sha256=canonical_execution_policy_sha256(**values),
    )


def canonical_execution_policy_sha256(
    *,
    manifest_sha256: str,
    label_definition_sha256: str,
    cost_assumptions_sha256: str,
    trading_calendar_id: str,
    trading_calendar_sha256: str,
    corporate_action_policy_sha256: str,
    fixed_terminal_date: date,
    entry_validity_trading_days: int,
    activation_rule: str,
    horizon_clock_rule: str,
    fill_rule: str,
    gap_rule: str,
    entry_gap_through_stop_rule: str,
    same_bar_ambiguity_rule: str,
    corporate_action_rule: str,
    rights_treatment_rule: str,
    dividend_return_convention: str,
    dividend_entitlement_rule: str,
    unfilled_rule: str,
) -> str:
    return canonical_payload_sha256(
        {
            "activation_rule": activation_rule,
            "corporate_action_policy_sha256": (
                corporate_action_policy_sha256
            ),
            "corporate_action_rule": corporate_action_rule,
            "cost_assumptions_sha256": cost_assumptions_sha256,
            "dividend_entitlement_rule": dividend_entitlement_rule,
            "dividend_return_convention": dividend_return_convention,
            "entry_validity_trading_days": entry_validity_trading_days,
            "entry_gap_through_stop_rule": entry_gap_through_stop_rule,
            "fill_rule": fill_rule,
            "fixed_terminal_date": fixed_terminal_date.isoformat(),
            "gap_rule": gap_rule,
            "horizon_clock_rule": horizon_clock_rule,
            "label_definition_sha256": label_definition_sha256,
            "manifest_sha256": manifest_sha256,
            "rights_treatment_rule": rights_treatment_rule,
            "same_bar_ambiguity_rule": same_bar_ambiguity_rule,
            "trading_calendar_id": trading_calendar_id,
            "trading_calendar_sha256": trading_calendar_sha256,
            "unfilled_rule": unfilled_rule,
        }
    )


def canonical_corporate_action_policy_sha256(
    convention: Literal["PRICE_RETURN", "TOTAL_RETURN"],
    prices_are_adjusted: bool,
    dividends_are_in_prices: bool,
    dividend_entitlement_rule: str = DIVIDEND_ENTITLEMENT_RULE,
    bar_price_basis: str = "RAW_AS_TRADED",
) -> str:
    return _corporate_action_policy_hash(
        convention,
        dividend_entitlement_rule,
        bar_price_basis,
        prices_are_adjusted,
        dividends_are_in_prices,
    )


def canonical_corporate_action_events_sha256(
    events: Sequence[CorporateActionEvent],
) -> str:
    return _corporate_action_events_hash(events)


def canonical_corporate_action_source_record_sha256(
    *,
    event_id: str,
    ticker: str,
    effective_date: date,
    kind: str,
    price_factor: float,
    quantity_factor: float,
    capital_call_per_pre_event_share: float,
    cash_per_share: float,
    source_id: str,
    source_definition_sha256: str,
    published_at: datetime,
) -> str:
    return canonical_payload_sha256(
        {
            "capital_call_per_pre_event_share": (
                capital_call_per_pre_event_share
            ),
            "cash_per_share": cash_per_share,
            "effective_date": effective_date.isoformat(),
            "event_id": event_id,
            "kind": kind,
            "price_factor": price_factor,
            "published_at": _utc_iso(published_at),
            "quantity_factor": quantity_factor,
            "source_definition_sha256": source_definition_sha256,
            "source_id": source_id,
            "ticker": ticker,
        }
    )


def canonical_outcome_bar_sha256(bar: OutcomeBar) -> str:
    return canonical_payload_sha256(bar.model_dump(mode="json"))


def canonical_outcome_source_record_sha256(
    *,
    source_id: str,
    source_definition_sha256: str,
    source_as_of: datetime,
    requested_start: date,
    requested_end: date,
    ticker: str,
    snapshot_sha256: str,
    bars_sha256: str,
    corporate_action_policy_sha256: str,
    corporate_action_events_sha256: str,
    previous_source_sha256: str | None,
) -> str:
    return canonical_payload_sha256(
        {
            "bars_sha256": bars_sha256,
            "corporate_action_events_sha256": (
                corporate_action_events_sha256
            ),
            "corporate_action_policy_sha256": (
                corporate_action_policy_sha256
            ),
            "previous_source_sha256": previous_source_sha256,
            "requested_end": requested_end.isoformat(),
            "requested_start": requested_start.isoformat(),
            "snapshot_sha256": snapshot_sha256,
            "source_as_of": _utc_iso(source_as_of),
            "source_definition_sha256": source_definition_sha256,
            "source_id": source_id,
            "ticker": ticker,
        }
    )


def canonical_geometry_adjustment_sha256(
    geometry: RecordedTradeGeometry | None,
    events: Sequence[CorporateActionEvent],
) -> str:
    if geometry is None:
        raise ShadowContractError("corporate-action adjustment needs geometry")
    return canonical_payload_sha256(
        {
            "corporate_actions": [
                _required_hash(event) for event in events
            ],
            "geometry_sha256": _required_hash(geometry),
            "price_basis": "RAW_AS_TRADED",
        }
    )


def canonical_bar_series_sha256(
    ticker: str,
    snapshot_id: str,
    snapshot_sha256: str,
    bars: Sequence[OutcomeBar],
) -> str:
    return _bars_hash(ticker, snapshot_id, snapshot_sha256, bars)


def _corporate_action_policy_hash(
    convention: str,
    dividend_entitlement_rule: str,
    bar_price_basis: str,
    prices_are_adjusted: bool,
    dividends_are_in_prices: bool,
) -> str:
    return canonical_payload_sha256(
        {
            "bar_price_basis": bar_price_basis,
            "dividend_return_convention": convention,
            "dividend_entitlement_rule": dividend_entitlement_rule,
            "dividends_are_in_prices": dividends_are_in_prices,
            "prices_are_adjusted": prices_are_adjusted,
        }
    )


def _corporate_action_events_hash(
    events: Sequence[CorporateActionEvent],
) -> str:
    return canonical_payload_sha256(
        {"events": [_required_hash(item) for item in events]}
    )


def _bars_hash(
    ticker: str,
    snapshot_id: str,
    snapshot_sha256: str,
    bars: Sequence[OutcomeBar],
) -> str:
    return canonical_payload_sha256(
        {
            "bars": [item.model_dump(mode="json") for item in bars],
            "snapshot_id": snapshot_id,
            "snapshot_sha256": snapshot_sha256,
            "ticker": ticker,
        }
    )


def _required_hash(model: _StrictFrozenModel | None) -> str:
    digest = canonical_sha256(model)
    if digest is None:
        raise ShadowContractError("cannot hash missing geometry")
    return digest


def _utc_iso(value: datetime) -> str:
    if value.utcoffset() is None:
        raise ValueError("canonical datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


__all__ = [
    "BAR_SERIES_VERSION",
    "CORPORATE_ACTION_POLICY_VERSION",
    "EXECUTION_POLICY_VERSION",
    "CorporateActionEvent",
    "CorporateActionPolicy",
    "FrozenExecutionPolicy",
    "FrozenBarSeries",
    "MaturationAuthorizationLoader",
    "MaturationRequest",
    "OUTCOME_ENGINE_VERSION",
    "OutcomeBar",
    "OutcomeLedger",
    "TRADING_CALENDAR_VERSION",
    "TradingCalendar",
    "canonical_bar_series_sha256",
    "canonical_corporate_action_events_sha256",
    "canonical_corporate_action_policy_sha256",
    "canonical_corporate_action_source_record_sha256",
    "canonical_execution_policy_sha256",
    "canonical_geometry_adjustment_sha256",
    "canonical_outcome_bar_sha256",
    "canonical_outcome_source_record_sha256",
    "canonical_trading_calendar_sha256",
    "build_execution_policy",
    "build_verified_outcome_lineage",
    "evaluate_all_horizons",
    "evaluate_horizon",
    "verify_outcome_against_request",
]
