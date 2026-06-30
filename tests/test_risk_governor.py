from core.risk_governor import evaluate_risk


def _candidate(**overrides):
    payload = {
        "ticker": "BBCA",
        "verdict": {
            "ticker": "BBCA",
            "rating": "BUY",
            "confidence": 0.75,
            "current_price": 1000,
            "entry_price_range": "950 - 1050",
            "target_price": 1290,  # (1290-1050)/(1050-930) = 2.0x R/R from prices
            "stop_loss": 930,
            "is_overvalued": False,
            "risk_reward_ratio": 2.0,
        },
    }
    verdict_overrides = overrides.pop("verdict", None)
    if verdict_overrides:
        payload["verdict"].update(verdict_overrides)
    payload.update(overrides)
    return payload


def test_current_price_inside_entry_range_is_deployable() -> None:
    decision = evaluate_risk(_candidate())

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert decision.reason_codes == ["price_inside_entry_range"]


def test_current_price_inside_entry_range_stays_deployable_in_normal_regime() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={
                "regime": "NORMAL",
                "defensive_triggered": False,
                "reasons": [],
            }
        )
    )

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert "market_regime_defensive" not in decision.reason_codes


def test_soft_overvalued_inside_range_stays_deployable() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 1040,
                "fair_value": 1000,
                "fair_value_base": 1000,
                "fair_value_high": 1150,
                "risk_overvalued": False,
                "is_overvalued": False,
            }
        )
    )

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert "overvalued" not in decision.reason_codes


def test_explicit_risk_overvalued_false_overrides_legacy_true() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 1040,
                "fair_value": 1000,
                "fair_value_high": 1150,
                "risk_overvalued": False,
                "is_overvalued": True,
            }
        )
    )

    assert decision.status == "deployable"
    assert "overvalued" not in decision.reason_codes


def test_explicit_risk_overvalued_rejects_even_if_legacy_flag_absent() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 1200,
                "fair_value": 1000,
                "fair_value_high": 1150,
                "risk_overvalued": True,
                "is_overvalued": False,
                "target_price": 1300,
            }
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "overvalued" in decision.reason_codes


def test_defensive_regime_downgrades_deployable_buy_to_watchlist() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={
                "regime": "DEFENSIVE",
                "defensive_triggered": True,
                "reasons": ["weekly_return_below_threshold"],
            }
        )
    )

    assert decision.status == "watchlist_only"
    assert decision.sizing_allowed is False
    assert "market_regime_defensive" in decision.reason_codes
    assert "price_inside_entry_range" in decision.reason_codes


def test_current_price_above_entry_high_waits_for_pullback() -> None:
    # target=1230: (1230-1050)/(1050-930)=1.5 — at floor, no rr_too_low; price above range.
    decision = evaluate_risk(
        _candidate(verdict={"current_price": 1100, "target_price": 1230})
    )

    assert decision.status == "wait_for_pullback"
    assert decision.sizing_allowed is False
    assert "price_above_entry_range" in decision.reason_codes


def test_target_at_or_below_current_price_rejects() -> None:
    # After P3: recomputed R/R=(1100-1050)/120=0.42 → rr_too_low fires first (hard reject).
    # upside_exhausted is added by price-position checks which run after hard-reject gate.
    decision = evaluate_risk(
        _candidate(verdict={"current_price": 1100, "target_price": 1100})
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rr_too_low" in decision.reason_codes


def test_invalid_or_missing_entry_range_rejects() -> None:
    decision = evaluate_risk(_candidate(verdict={"entry_price_range": None}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_entry_range" in decision.reason_codes


def test_invalid_price_reject_preserves_upstream_rr_reason() -> None:
    decision = evaluate_risk(
        _candidate(
            metadata={"reason_codes": ["rr_too_low"]},
            verdict={"entry_price_range": None, "target_price": None},
        )
    )

    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes
    assert "invalid_entry_range" in decision.reason_codes
    assert "missing_target_price" in decision.reason_codes


def test_non_finite_prices_are_rejected_as_missing() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": float("nan"),
                "entry_price_range": "950 - 1050",
                "target_price": float("inf"),
            }
        )
    )

    assert decision.status == "reject"
    assert "missing_current_price" in decision.reason_codes
    assert "missing_target_price" in decision.reason_codes

def test_preflight_noise_reject_does_not_report_missing_trade_levels() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "rating": "HOLD",
                "confidence": 0.40,
                "entry_price_range": None,
                "target_price": None,
                "stop_loss": None,
                "risk_flags": ["PREFLIGHT_NOISE_REJECT"],
            }
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert decision.reason_codes == ["preflight_noise_reject"]
    assert "entry/target/stop sengaja tidak dibuat" in decision.message


def test_defensive_regime_keeps_invalid_setup_rejected() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={"regime": "DEFENSIVE"},
            verdict={"entry_price_range": None},
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_entry_range" in decision.reason_codes
    assert "market_regime_defensive" not in decision.reason_codes


def test_stop_loss_at_or_above_current_price_rejects() -> None:
    decision = evaluate_risk(_candidate(verdict={"stop_loss": 1000}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "invalid_stop_loss" in decision.reason_codes


def test_current_price_below_entry_low_is_watchlist_only() -> None:
    # stop=890: (1290-1050)/(1050-890)=240/160=1.5 — at floor, no rr_too_low; price below range.
    decision = evaluate_risk(
        _candidate(verdict={"current_price": 900, "stop_loss": 890})
    )

    assert decision.status == "watchlist_only"
    assert decision.sizing_allowed is False
    assert "price_below_entry_range" in decision.reason_codes


def test_avoid_verdict_rejects_even_inside_entry_range() -> None:
    # target=1100: (1100-1050)/120=0.42 → rr_too_low matches original spirit (bad R/R).
    decision = evaluate_risk(
        _candidate(
            verdict={
                "rating": "AVOID",
                "confidence": 0.22,
                "is_overvalued": True,
                "risk_reward_ratio": 0.56,
                "target_price": 1100,
                "weighted_reasoning": (
                    "Absence of technical indicators (INSUFFICIENT_DATA)."
                ),
            }
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rating_not_buyable" in decision.reason_codes
    assert "low_confidence" in decision.reason_codes
    assert "overvalued" in decision.reason_codes
    assert "rr_too_low" in decision.reason_codes
    assert "insufficient_technical_data" in decision.reason_codes


def test_defensive_regime_keeps_avoid_rejected_not_watchlist() -> None:
    decision = evaluate_risk(
        _candidate(
            market_regime={"regime": "DEFENSIVE"},
            verdict={
                "rating": "AVOID",
                "confidence": 0.22,
                "is_overvalued": True,
                "risk_reward_ratio": 0.56,
            },
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rating_not_buyable" in decision.reason_codes
    assert "market_regime_defensive" not in decision.reason_codes


def test_large_cap_rr_above_tier_threshold_is_not_too_low() -> None:
    decision = evaluate_risk(
        _candidate(
            ticker="BBRI",
            metadata={"market_cap_idr": 400_000_000_000_000},
            verdict={
                "ticker": "BBRI",
                "rating": "AVOID",
                "confidence": 0.25,
                "risk_reward_ratio": 1.38,
                "weighted_reasoning": "Counter-trend bounce below MA200.",
            },
        )
    )

    assert decision.status == "reject"
    assert "rating_not_buyable" in decision.reason_codes
    assert "low_confidence" in decision.reason_codes
    assert "counter_trend_setup" in decision.reason_codes
    # P3: 1.38x < 2.5 counter-trend floor → rr_too_low now also fires
    assert "rr_too_low" in decision.reason_codes


def test_default_tier_rr_below_default_threshold_is_too_low() -> None:
    # target=1216: (1216-1050)/120=1.383 < 1.5 floor → rr_too_low.
    decision = evaluate_risk(
        _candidate(
            ticker="CYBR",
            metadata={"market_cap_idr": 5_000_000_000_000},
            verdict={
                "ticker": "CYBR",
                "rating": "AVOID",
                "confidence": 0.25,
                "risk_reward_ratio": 1.38,
                "target_price": 1216,
            },
        )
    )

    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes


def test_implausible_rr_is_hard_rejected() -> None:
    # target=1700: (1700-1050)/120=5.42x — recomputed from prices, above ceiling.
    decision = evaluate_risk(_candidate(verdict={"risk_reward_ratio": 22.3, "target_price": 1700}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rr_implausible" in decision.reason_codes


def test_high_but_plausible_rr_stays_deployable() -> None:
    decision = evaluate_risk(_candidate(verdict={"risk_reward_ratio": 4.9}))

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True
    assert "rr_implausible" not in decision.reason_codes


def test_recomputed_rr_overrides_llm_inflated_ratio() -> None:
    """P3: price-based recompute wins over LLM-provided ratio when both are available."""
    # LLM claims R/R=5.0; actual from prices (1150-1050)/120=0.83 → rr_too_low.
    decision = evaluate_risk(
        _candidate(verdict={"risk_reward_ratio": 5.0, "target_price": 1150,
                            "risk_overvalued": False, "is_overvalued": False})
    )

    assert "rr_implausible" not in decision.reason_codes  # 5.0 is NOT used
    assert "rr_too_low" in decision.reason_codes          # 0.83 IS used
    assert decision.sizing_allowed is False


def test_buy_low_confidence_is_hard_rejected() -> None:
    """P2: BUY with confidence below threshold → sizing blocked, not conditional."""
    decision = evaluate_risk(
        _candidate(verdict={"confidence": 0.35, "risk_overvalued": False, "is_overvalued": False})
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "low_confidence" in decision.reason_codes


def test_fv_unmeasurable_yields_conditional_deployable() -> None:
    """FV absent → risk_overvalued=None → fv_unmeasurable → conditional, not deployable."""
    decision = evaluate_risk(
        _candidate(verdict={"risk_overvalued": None, "is_overvalued": None})
    )

    assert "fv_unmeasurable" in decision.reason_codes
    assert "overvalued" not in decision.reason_codes
    assert decision.status == "conditional_deployable"
    assert decision.sizing_allowed is False


def test_fv_explicitly_false_is_not_flagged_as_unmeasurable() -> None:
    """Explicit risk_overvalued=False (safe) must not produce fv_unmeasurable."""
    decision = evaluate_risk(
        _candidate(verdict={"risk_overvalued": False, "is_overvalued": False})
    )

    assert "fv_unmeasurable" not in decision.reason_codes
    assert "overvalued" not in decision.reason_codes
    assert decision.status == "deployable"
    assert decision.sizing_allowed is True


def test_rr_exactly_at_ceiling_is_rejected() -> None:
    # target=1650: (1650-1050)/120=5.0 exactly — recomputed hits the ceiling.
    decision = evaluate_risk(_candidate(verdict={"risk_reward_ratio": 5.0, "target_price": 1650}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "rr_implausible" in decision.reason_codes


def test_hold_low_confidence_inside_entry_is_hard_rejected() -> None:
    # After P2: low_confidence is a HARD_REJECT_CODE — no longer conditional.
    decision = evaluate_risk(
        _candidate(
            verdict={
                "rating": "HOLD",
                "confidence": 0.41,
                "weighted_reasoning": "Counter-trend bounce below MA200.",
            }
        )
    )

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "low_confidence" in decision.reason_codes


def test_sentiment_insufficient_data_does_not_reject_when_technicals_exist() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "weighted_reasoning": (
                    "Technicals are constructive, but sentiment is INSUFFICIENT_DATA."
                )
            }
        )
    )

    assert decision.status == "deployable"
    assert "insufficient_technical_data" not in decision.reason_codes


def test_missing_rr_is_recomputed_and_rejected_below_floor() -> None:
    # Regression: a verdict without risk_reward_ratio used to skip the floor
    # check entirely; the governor now recomputes the canonical entry_high R/R.
    decision = evaluate_risk(
        _candidate(
            verdict={
                "risk_reward_ratio": None,
                "target_price": 1150,
                "stop_loss": 930,
            }
        )
    )

    # (1150 - 1050) / (1050 - 930) = 0.83x — below any tier floor.
    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes


def test_missing_rr_is_recomputed_and_passes_above_floor() -> None:
    decision = evaluate_risk(
        _candidate(
            verdict={
                "risk_reward_ratio": None,
                "target_price": 1300,
                "stop_loss": 930,
            }
        )
    )

    # (1300 - 1050) / (1050 - 930) = 2.08x — clears the floor.
    assert decision.status == "deployable"
    assert "rr_too_low" not in decision.reason_codes


def test_unspaced_entry_range_parses_as_positive_bounds() -> None:
    # Regression: "950-1050" used to parse the second bound as -1050 and
    # false-reject the setup with invalid_entry_range.
    decision = evaluate_risk(_candidate(verdict={"entry_price_range": "950-1050"}))

    assert decision.status == "deployable"
    assert "invalid_entry_range" not in decision.reason_codes


def test_counter_trend_hold_with_string_rr_does_not_crash() -> None:
    # Regression: _is_conditional_setup used a raw float() that raised on
    # formatted ratios like "3.8x".
    decision = evaluate_risk(
        _candidate(
            technical_indicators={"ma200_context": "BELOW"},
            verdict={
                "rating": "HOLD",
                "risk_reward_ratio": "3.8x",
                "target_price": 1510,
                "stop_loss": 930,
            },
        )
    )

    # R/R >= 3.5 with only counter-trend + hold soft flags deploys directly.
    assert decision.status == "deployable"


def test_thousand_dot_entry_range_parses_as_full_idr_prices() -> None:
    # Regression: "4.300 - 4.600" used to parse as (4.3, 4.6), making a price
    # of 4500 look like it was far above the entry range.
    # target=5350: (5350-4600)/(4600-4100)=750/500=1.5 — recomputed at floor, no rr_too_low.
    decision = evaluate_risk(
        _candidate(
            verdict={
                "current_price": 4500,
                "entry_price_range": "4.300 - 4.600",
                "target_price": "Rp 5.350",
                "stop_loss": "Rp 4.100",
                "risk_reward_ratio": 2.0,
            }
        )
    )

    assert decision.entry_low == 4300.0
    assert decision.entry_high == 4600.0
    assert decision.target_price == 5350.0
    assert decision.stop_loss == 4100.0
    assert decision.status == "deployable"


def test_stale_candidate_rr_ratio_does_not_bypass_recompute() -> None:
    # Regression: when the verdict ratio is missing, a stale top-level
    # candidate rr_ratio used to win over a fresh recompute from prices.
    decision = evaluate_risk(
        _candidate(
            rr_ratio=2.4,  # stale echo from an earlier run
            verdict={
                "risk_reward_ratio": None,
                "target_price": 1150,
                "stop_loss": 930,
            },
        )
    )

    # Fresh recompute: (1150 - 1050) / (1050 - 930) = 0.83x < floor.
    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes


def test_historically_expensive_appended_as_soft_code() -> None:
    # P8: stock at 95th-percentile PE → "historically_expensive" in reason_codes.
    # Soft flag: NOT in HARD_REJECT_CODES, setup becomes conditional_deployable.
    decision = evaluate_risk(
        _candidate(
            metadata={"valuation_band_context": "HISTORICALLY_EXPENSIVE (PE 95th pct)"},
        )
    )

    assert "historically_expensive" in decision.reason_codes
    assert decision.status == "conditional_deployable"
    assert decision.sizing_allowed is False


def test_historically_expensive_does_not_hard_reject() -> None:
    # P8: "historically_expensive" alone must not block BUY with clean R/R.
    from core.risk_governor import HARD_REJECT_CODES

    assert "historically_expensive" not in HARD_REJECT_CODES


def test_historically_expensive_absent_when_band_context_is_normal() -> None:
    # No flag when valuation_band_context does not contain HISTORICALLY_EXPENSIVE.
    decision = evaluate_risk(
        _candidate(
            metadata={"valuation_band_context": "FAIRLY_VALUED (PE 50th pct)"},
        )
    )

    assert "historically_expensive" not in decision.reason_codes


def test_counter_trend_hold_llm_rr_diverges_from_recomputed_is_rejected() -> None:
    # bug_008 + P3: LLM claims R/R=3.8 but recomputed from prices gives 1.58x.
    # 1.58x < 2.5 counter-trend floor → rr_too_low → hard reject.
    decision = evaluate_risk(
        _candidate(
            verdict={
                "rating": "HOLD",
                "confidence": 0.75,
                "entry_price_range": "950 - 1050",
                "target_price": 1240,   # (1240-1050)/(1050-930) = 1.58x recomputed
                "stop_loss": 930,
                "risk_reward_ratio": 3.8,  # LLM-inflated; must NOT win
                "current_price": 1000,
                "weighted_reasoning": "Counter-trend bounce below MA200.",
            },
        )
    )

    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes
    assert "counter_trend_setup" in decision.reason_codes


def test_counter_trend_rr_below_floor_is_rejected() -> None:
    # P3: R/R 1.9x clears the default 1.5x tier floor but falls below the 2.5x
    # counter-trend floor → rr_too_low must fire → hard reject.
    decision = evaluate_risk(
        _candidate(
            technical_indicators={"ma200_context": "BELOW"},
            verdict={
                "rating": "BUY",
                "confidence": 0.72,
                "entry_price_range": "950 - 1050",
                "target_price": 1278,   # (1278-1050)/(1050-930) = 1.9x recomputed
                "stop_loss": 930,
                "risk_reward_ratio": 1.9,
                "current_price": 1000,
            },
        )
    )

    assert decision.status == "reject"
    assert "rr_too_low" in decision.reason_codes
    assert "counter_trend_setup" in decision.reason_codes


def test_counter_trend_rr_above_floor_stays_conditional() -> None:
    # P3: R/R 2.8x clears the 2.5x counter-trend floor but is below the 3.5x
    # short-circuit bypass → counter_trend_setup soft flag → conditional_deployable.
    decision = evaluate_risk(
        _candidate(
            technical_indicators={"ma200_context": "BELOW"},
            verdict={
                "rating": "BUY",
                "confidence": 0.72,
                "entry_price_range": "950 - 1050",
                "target_price": 1386,   # (1386-1050)/(1050-930) = 2.8x recomputed
                "stop_loss": 930,
                "risk_reward_ratio": 2.8,
                "current_price": 1000,
            },
        )
    )

    assert decision.status == "conditional_deployable"
    assert "counter_trend_setup" in decision.reason_codes
    assert "rr_too_low" not in decision.reason_codes


# ── Task F: Liquidity gate ────────────────────────────────────────────────────


def _liquid_candidate(avg_volume: float | None = None, **overrides):
    """Candidate with configurable avg_volume in risk_context."""
    c = _candidate(**overrides)
    if avg_volume is not None:
        c.setdefault("risk_context", {})["avg_volume"] = avg_volume
    return c


def test_illiquid_ticker_hard_rejects() -> None:
    # current_price=1000, avg_volume=1_500_000 → ADT = 1.5B < 2B
    decision = evaluate_risk(_liquid_candidate(avg_volume=1_500_000))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "insufficient_liquidity" in decision.reason_codes


def test_low_liquidity_ticker_conditional() -> None:
    # current_price=1000, avg_volume=4_000_000 → ADT = 4B (between 2B and 10B)
    decision = evaluate_risk(_liquid_candidate(avg_volume=4_000_000))

    assert decision.status == "conditional_deployable"
    assert decision.sizing_allowed is False
    assert "low_liquidity" in decision.reason_codes


def test_liquid_ticker_unaffected() -> None:
    # current_price=1000, avg_volume=15_000_000 → ADT = 15B >= 10B → normal flow
    decision = evaluate_risk(_liquid_candidate(avg_volume=15_000_000))

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True


# ── Task G: Ex-date enforcement ──────────────────────────────────────────────


def _exdate_candidate(exdate_tier: str, **overrides):
    c = _candidate(**overrides)
    c.setdefault("risk_context", {})["exdate_tier"] = exdate_tier
    return c


def test_exdate_avoid_hard_rejects() -> None:
    decision = evaluate_risk(_exdate_candidate("AVOID"))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "exdate_imminent" in decision.reason_codes


def test_exdate_cap65_is_conditional() -> None:
    decision = evaluate_risk(_exdate_candidate("CAP_65"))

    assert decision.status == "conditional_deployable"
    assert decision.sizing_allowed is False
    assert "exdate_cap65" in decision.reason_codes


def test_exdate_clear_unaffected() -> None:
    decision = evaluate_risk(_exdate_candidate("CLEAR"))

    assert decision.status == "deployable"
    assert decision.sizing_allowed is True


def test_missing_adt_degrades_gracefully() -> None:
    # No avg_volume in risk_context or technical_indicators → gate is skipped
    decision = evaluate_risk(_candidate())

    # Default _candidate has no avg_volume → no liquidity gate → deployable as before
    assert decision.status == "deployable"
    assert "insufficient_liquidity" not in decision.reason_codes
    assert "low_liquidity" not in decision.reason_codes


# ---------------------------------------------------------------------------
# P11: T+2 minimum hold enforcement
# ---------------------------------------------------------------------------


def test_t2_hold_warning_when_hold_days_below_settlement() -> None:
    # Candidate explicitly declares hold_days=1 — less than IDX T+2 settlement (2 days)
    decision = evaluate_risk(_candidate(hold_days=1))
    assert "t2_hold_warning" in decision.reason_codes
    assert decision.status == "conditional_deployable"
    assert decision.sizing_allowed is False


def test_t2_hold_not_triggered_when_hold_days_absent() -> None:
    # Default candidate has no hold_days field → T+2 gate silent
    decision = evaluate_risk(_candidate())
    assert "t2_hold_warning" not in decision.reason_codes


def test_t2_hold_not_triggered_when_hold_days_meets_minimum() -> None:
    # hold_days=2 equals MIN_HOLD_DAYS → no warning
    decision = evaluate_risk(_candidate(hold_days=2))
    assert "t2_hold_warning" not in decision.reason_codes


# ---------------------------------------------------------------------------
# P12: ARA entry-risk hard reject
# ---------------------------------------------------------------------------


def test_ara_entry_risk_high_is_hard_rejected() -> None:
    """ara_entry_risk=HIGH is in HARD_REJECT_CODES → sizing_allowed=False."""
    decision = evaluate_risk(_candidate(ara_entry_risk="HIGH"))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "ara_entry_risk_high" in decision.reason_codes


def test_ara_entry_risk_high_via_metadata_is_hard_rejected() -> None:
    """ara_entry_risk HIGH in candidate['metadata'] is read and hard-rejected."""
    decision = evaluate_risk(_candidate(metadata={"ara_entry_risk": "HIGH"}))

    assert decision.status == "reject"
    assert decision.sizing_allowed is False
    assert "ara_entry_risk_high" in decision.reason_codes


def test_ara_entry_risk_medium_is_not_hard_rejected() -> None:
    """MEDIUM ara_entry_risk is a soft signal, not a hard reject."""
    decision = evaluate_risk(_candidate(ara_entry_risk="MEDIUM"))

    assert decision.sizing_allowed is True
    assert "ara_entry_risk_high" not in decision.reason_codes
