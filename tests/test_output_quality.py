"""Task 30 — LLM Output Quality Verification (8 quality gates for CIOVerdict)."""

from __future__ import annotations

from schemas.debate import CIOVerdict
from utils.quality_checks import check_verdict_quality


def _buy(
    confidence: float = 0.75,
    entry: str = "1000 - 1050",
    target: float = 1150.0,
    stop: float = 900.0,
    **kwargs,
) -> CIOVerdict:
    """Minimal valid BUY verdict for parametric tests."""
    return CIOVerdict(
        rating="BUY",
        confidence=confidence,
        entry_price_range=entry,
        target_price=target,
        stop_loss=stop,
        fair_value=1200.0,
        current_price=1010.0,
        **kwargs,
    )


# ── Gate 1: wait_and_see triggers on low confidence ──────────────────────────

def test_wait_and_see_triggers_below_confidence_threshold():
    v = _buy(confidence=0.50)
    assert v.wait_and_see is True


# ── Gate 2: wait_and_see triggers on bad R/R ─────────────────────────────────

def test_wait_and_see_triggers_on_bad_rr():
    # entry_high=1050, target=1060 → R/R = (1060-1050)/(1050-900) = 0.067 < 1.0
    v = _buy(confidence=0.80, target=1060.0)
    assert v.wait_and_see is True


# ── Gate 3: wait_and_see clears on a good setup ──────────────────────────────

def test_wait_and_see_false_on_good_setup():
    # entry 1000-1020, target=1150, stop=970
    # R/R = (1150-1020)/(1020-970) = 130/50 = 2.6 ≥ 1.0
    v = _buy(confidence=0.75, entry="1000 - 1020", target=1150.0, stop=970.0)
    assert v.wait_and_see is False


# ── Gate 4: expected_return string formatted from entry midpoint ──────────────

def test_expected_return_formatted_from_entry_midpoint():
    # entry_mid = (1000+1050)/2 = 1025; target = 1150
    # gain = (1150-1025)/1025 * 100 ≈ +12.2%
    v = _buy()
    assert v.expected_return is not None
    assert v.expected_return.startswith("+")
    assert "%" in v.expected_return
    pct = float(v.expected_return.replace("%", ""))
    assert abs(pct - 12.2) < 0.2


# ── Gate 5: rating downgrade BUY→HOLD when gain_pct < 3% ────────────────────

def test_rating_downgraded_to_hold_on_low_gain():
    # entry_mid = (1000+1050)/2 = 1025; target=1055 → gain=2.93% < 3%
    v = _buy(target=1055.0)
    assert v.rating == "HOLD"


# ── Gate 6: key_risks auto-appended when fair_value is None ──────────────────

def test_key_risks_appended_when_fair_value_missing():
    v = CIOVerdict(
        rating="HOLD",
        confidence=0.65,
        fair_value=None,
        key_risks=[],
    )
    assert len(v.key_risks) >= 1
    assert any("fundamental" in r.lower() for r in v.key_risks)


def test_preflight_fair_value_status_is_not_reported_as_data_unavailable():
    v = CIOVerdict(
        rating="HOLD",
        confidence=0.0,
        fair_value=None,
        fair_value_status="NOT_EVALUATED_PREFLIGHT",
        key_risks=[],
    )

    assert any("NOT_EVALUATED_PREFLIGHT" in risk for risk in v.key_risks)
    assert not any("validasi fundamental" in risk.lower() for risk in v.key_risks)


# ── Gate 7: check_verdict_quality flags empty narrative on actionable verdict ─

def test_check_quality_flags_missing_narrative_on_buy():
    # Use tight entry range so R/R ≥ 1.0 (keeps rating=BUY after validator)
    v = _buy(entry="1000 - 1020", target=1150.0, stop=970.0)
    issues = check_verdict_quality(v)
    issue_text = " ".join(issues)
    assert "weighted_reasoning" in issue_text
    assert "critical_risk_factor" in issue_text
    assert "key_risks" in issue_text
    assert "key_catalysts" in issue_text
    assert "summary" in issue_text


# ── Gate 8: check_verdict_quality passes on a fully-populated verdict ─────────

def test_check_quality_passes_full_buy_verdict():
    v = _buy(
        entry="1000 - 1020",
        target=1150.0,
        stop=970.0,
        weighted_reasoning="Technical breakout confirmed by VWAP and volume.",
        critical_risk_factor="Global risk-off could trigger stop at 970.",
        key_catalysts=["Strong Q1 earnings beat", "Index inclusion catalyst"],
        key_risks=["BI rate hike risk", "USD/IDR above 16500"],
        summary="BBCA forming a bull flag with institutional VWAP support. Entry 1000-1020, target 1150, stop 970.",
    )
    issues = check_verdict_quality(v)
    assert issues == [], f"Unexpected quality issues: {issues}"
