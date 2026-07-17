import re

from rich.console import Console

from services.explainability_auditor import (
    AgentVoteSummary,
    AuditPacket,
    EvidenceItem,
)
from services.report_formatter import MarkdownFormatter, RichFormatter


def _mock_result(rating: str = "BUY") -> dict:
    return {
        "ticker": "BBCA",
        "verdict": {
            "ticker": "BBCA",
            "rating": rating,
            "confidence": 0.7,
            "current_price": 9000,
            "fair_value": 10500,
            "entry_price_range": "8800 - 9100",
            "target_price": 10200,
            "stop_loss": 8500,
            "risk_reward_ratio": 2.4,
            "timeframe": "5-20 Trading Days",
            "execution_horizon_days": 10,
            "summary": "BBCA remains attractive with measured risk.",
            "key_risks": ["IHSG volatility", "Margin pressure"],
            "key_catalysts": ["Strong liquidity", "Banking sector rotation"],
        },
        "debate_rounds": 3,
        "consensus_reached": True,
        "consensus_method": "confidence_winner",
        "winner_agent": "bull",
        "agent_votes": [
            {
                "agent": "bull",
                "position": "BUY",
                "confidence": 0.7,
                "supporting_winner": True,
            },
            {
                "agent": "bear",
                "position": "AVOID",
                "confidence": 0.6,
                "supporting_winner": False,
            },
            {
                "agent": "chartist",
                "position": "BUY",
                "confidence": 0.65,
                "supporting_winner": True,
            },
            {
                "agent": "fundamental_scout",
                "position": "BUY",
                "confidence": 0.68,
                "supporting_winner": True,
            },
            {
                "agent": "devils_advocate",
                "position": "AVOID",
                "confidence": 0.95,
                "supporting_winner": False,
            },
        ],
        "debate_history": [
            {
                "role": "bull",
                "content": "Strong bull case.\n\nPosition: BUY\nAgent Confidence: 0.70",
            },
            {
                "role": "bear",
                "content": "Bear case highlights valuation risk.\n\nPosition: AVOID\nAgent Confidence: 0.60",
            },
            {
                "role": "devils_advocate",
                "content": "What is the invalidation scenario?",
            },
        ],
        "risk_governor": {"status": "deployable"},
        "news_sentiment": "neutral",
        "news_confidence_adjustment": 0.0,
        "metadata": {
            "run_id": "run-123",
            "generated_at": "2026-05-20T08:00:00+07:00",
        },
    }


def _mock_packet() -> AuditPacket:
    return AuditPacket(
        ticker="BBCA",
        run_id="run-123",
        generated_at="2026-05-20T08:00:00+07:00",
        verdict_rating="BUY",
        verdict_confidence=0.7,
        consensus_method="confidence_winner",
        consensus_reached=True,
        debate_rounds=3,
        winner_agent="bull",
        winner_position="BUY",
        winner_confidence=0.7,
        dissenting_agents=["bear"],
        dissent_rate=0.2,
        agent_votes=[
            AgentVoteSummary(
                agent="bull",
                position="BUY",
                confidence=0.7,
                round_num=1,
                supporting_winner=True,
                summary="Strong bull case.",
            )
        ],
        evidence_used=[
            EvidenceItem(
                category="price",
                content="Current Price: 9000",
                source="mock",
                is_stale=False,
                freshness_note=None,
            )
        ],
        key_bull_argument="Strong bull case.",
        key_bear_argument="Bear case highlights valuation risk.",
        devils_advocate_question="What is the invalidation scenario?",
        data_freshness_ok=True,
        stale_sources=[],
        missing_fields=[],
        one_line_summary="BBCA BUY with 70% confidence.",
    )


def test_rating_label_maps_core_ratings() -> None:
    formatter = RichFormatter()

    assert formatter._rating_emoji("BUY") == "BUY"
    assert formatter._rating_emoji("HOLD") == "HOLD"
    assert formatter._rating_emoji("AVOID") == "AVOID"
    assert formatter._rating_emoji("STRONG_BUY") == "BUY"
    assert formatter._rating_emoji("SELL") == "AVOID"
    assert formatter._rating_style("STRONG_BUY") == "bold green"
    assert formatter._rating_style("SELL") == "bold red"


def test_confidence_bar_mixed_fill() -> None:
    bar = RichFormatter()._confidence_bar(0.6)

    assert "█" in bar
    assert "░" in bar
    assert "60%" in bar


def test_confidence_bar_full_fill() -> None:
    bar = RichFormatter()._confidence_bar(1.0)

    assert "█" in bar
    assert "░" not in bar


def test_risk_governor_deployable_line() -> None:
    line = RichFormatter()._risk_governor_line({"status": "deployable"})

    assert "Execution" in line


def test_risk_governor_reject_line() -> None:
    line = RichFormatter()._risk_governor_line({"status": "reject"})

    assert "System rejected" in line


def test_risk_governor_conditional_line() -> None:
    line = RichFormatter()._risk_governor_line({"status": "conditional_deployable"})

    assert "Conditional" in line


def test_risk_governor_defensive_watchlist_line_is_plain_language() -> None:
    line = RichFormatter()._risk_governor_line(
        {
            "status": "watchlist_only",
            "reason_codes": ["market_regime_defensive"],
        }
    )

    assert line == "No sizing (defensive market)"
    assert "market_regime_defensive" not in line


def test_generate_ticker_report_contains_title_and_ticker(tmp_path) -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result(), _mock_packet())
    path = tmp_path / "latest_report.md"
    path.write_text(report, encoding="utf-8")

    assert "Analysis Report" in path.read_text(encoding="utf-8")
    assert "BBCA" in report


def test_generate_ticker_report_contains_execution_horizon() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "| **Timeframe** | 5-20 Trading Days |" in report
    assert "| **Execution Horizon** | 10 trading days |" in report


def test_ticker_report_uses_canonical_execution_regime_authority() -> None:
    result = _mock_result()
    result.update(
        {
            "execution_regime": "DEFENSIVE",
            "execution_regime_reason": "rule_based_defensive_override",
            "trend_regime": {"label": "SIDEWAYS", "confidence": 0.9467},
            "volatility_regime": "HIGH",
        }
    )
    result["metadata"]["regime"] = "NORMAL"

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| **Execution Regime** | DEFENSIVE |" in report
    assert "| **Execution Regime Reason** | rule_based_defensive_override |" in report
    assert "| **Trend Regime (diagnostic)** | SIDEWAYS (94.7%) |" in report
    assert "| **Volatility Regime (diagnostic)** | HIGH |" in report
    assert "| **Legacy Regime (diagnostic)** |" not in report
    assert "| **Execution Regime** | NORMAL |" not in report


def test_ticker_report_marks_old_regime_as_legacy_diagnostic() -> None:
    result = _mock_result()
    result["metadata"]["regime"] = "DEFENSIVE"

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| **Execution Regime** | UNKNOWN |" in report
    assert (
        "| **Execution Regime Reason** | "
        "legacy_artifact_missing_execution_regime |"
    ) in report
    assert "| **Legacy Regime (diagnostic)** | DEFENSIVE |" in report


def test_generate_ticker_report_buy_contains_catalyst_section() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result("BUY"))

    assert "Potential Catalysts" in report


def test_generate_ticker_report_avoid_skips_catalyst_section() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result("AVOID"))

    assert "Potential Catalysts" not in report


def test_generate_ticker_report_normalizes_strong_buy_rating() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result("STRONG_BUY"))

    assert "| **Recommendation** | **BUY** |" in report
    assert "Potential Catalysts" in report


def test_generate_ticker_report_contains_all_agent_names() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    for name in (
        "Bull",
        "Bear",
        "Chartist",
        "Fundamental Scout",
        "Sentiment Specialist",
        "Devil's Advocate",
    ):
        assert name in report


def test_vote_table_includes_sentiment_specialist_in_order() -> None:
    result = _mock_result()
    result["agent_votes"].insert(
        4,
        {
            "agent": "sentiment_specialist",
            "position": "HOLD",
            "confidence": 0.55,
            "supporting_winner": False,
        },
    )

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| Sentiment Specialist | HOLD | 55% |" in report
    assert report.index("Fundamental Scout") < report.index("Sentiment Specialist")
    assert report.index("Sentiment Specialist") < report.index("Devil's Advocate")


def test_markdown_risk_governor_does_not_instantiate_rich_formatter(
    monkeypatch,
) -> None:
    def fail_if_called(self, risk):
        raise AssertionError("MarkdownFormatter should not call RichFormatter")

    monkeypatch.setattr(RichFormatter, "_risk_governor_line", fail_if_called)

    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "| **Risk Governor** | Execution ready |" in report


def test_generate_ticker_report_replaces_advocatus_with_decision_summary() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "Decision Summary & Agent Rationale" in report
    assert "Agent choice distribution" in report
    assert "Advocatus Diaboli" not in report


def test_generate_ticker_report_contains_confidence_percent() -> None:
    confidence = _mock_result()["verdict"]["confidence"]
    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "{:.0%}".format(confidence) in report


def test_generate_ticker_report_shows_data_quality_warnings() -> None:
    result = _mock_result()
    result["metadata"]["news_fetch_failure"] = {
        "stage": "build_bundle",
        "type": "OSError",
        "message": "news provider unavailable",
    }
    result["metadata"]["rag_selection_failure"] = {
        "stage": "build_bundle",
        "type": "OSError",
        "message": "rag evidence log locked",
    }

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "Data Quality Warnings" in report
    assert "News fetch failure: build_bundle/OSError" in report
    assert "news provider unavailable" in report
    assert "RAG selection failure: build_bundle/OSError" in report
    assert "rag evidence log locked" in report


def test_generate_ticker_report_shows_forecast_quality_flags() -> None:
    result = _mock_result()
    result["forecast_report"] = {
        "forecast_status": "ZERO_WEIGHT",
        "failure_reason": "all_validated_return_models_disqualified",
        "data_quality_flags": ["ocf_missing", "validation_status:failed"],
    }
    result["forecast_ev_ignored_reason"] = "validation_failed"

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "Forecast status: ZERO_WEIGHT (all_validated_return_models_disqualified)" in report
    assert "Forecast data quality: ocf_missing, validation_status:failed" in report
    assert "Forecast EV ignored: validation_failed" in report

def test_render_ticker_panel_shows_data_quality_warnings() -> None:
    console = Console(record=True, width=140)
    formatter = RichFormatter(console=console)
    result = _mock_result()
    result["metadata"]["cio_parse_failure"] = {
        "stage": "json_parse",
        "type": "JSONDecodeError",
        "message": "invalid JSON",
    }

    formatter.render_ticker_panel(result)

    output = console.export_text()
    assert "Data Quality" in output
    assert "CIO parse fallback: json_parse/JSONDecodeError" in output
    assert "invalid JSON" in output


def test_render_ticker_panel_shows_forecast_quality_flags() -> None:
    console = Console(record=True, width=140)
    formatter = RichFormatter(console=console)
    result = _mock_result()
    result["forecast_report"] = {
        "forecast_status": "MODEL_FAILED",
        "failure_reason": "all_return_models_unavailable",
        "data_quality_flags": ["validation_status:failed"],
    }
    result["forecast_ev_ignored_reason"] = "validation_failed"

    formatter.render_ticker_panel(result)
    output = console.export_text()

    assert "Forecast status: MODEL_FAILED (all_return_models_unavailable)" in output
    assert "Forecast data quality: validation_status:failed" in output
    assert "Forecast EV ignored: validation_failed" in output


def test_generate_batch_summary_contains_run_id_and_title() -> None:
    report = MarkdownFormatter().generate_batch_summary([_mock_result()], "run-123")

    assert "run-123" in report
    assert "Batch Analysis Summary" in report


def test_generate_batch_summary_contains_buy_ticker_in_results_table() -> None:
    report = MarkdownFormatter().generate_batch_summary([_mock_result()], "run-123")

    assert "BBCA" in report
    assert "| BUY |" in report


def test_markdown_reports_include_market_snapshot_provenance() -> None:
    result = _mock_result()
    result["metadata"]["market_snapshot"] = {
        "snapshot_id": "snap-bbca-20260713",
        "data_hash": "sha256-bbca-full",
    }

    ticker_report = MarkdownFormatter().generate_ticker_report(result)
    batch_report = MarkdownFormatter().generate_batch_summary([result], "run-123")

    assert "snap-bbca-20260713" in ticker_report
    assert "sha256-bbca-full" in ticker_report
    assert "snap-bbca-20260713" in batch_report
    assert "sha256-bbca-full" in batch_report


def test_batch_summary_uses_execution_contract_for_actionability() -> None:
    no_trade = _mock_result("BUY")
    no_trade["execution_decision"] = {
        "execution_status": "NO_TRADE",
        "decision_source": "risk_guard",
        "actionable": False,
    }
    no_trade["execution_status"] = "NO_TRADE"
    no_trade["decision_source"] = "risk_guard"

    report = MarkdownFormatter().generate_batch_summary([no_trade], "run-123")
    ticker_report = MarkdownFormatter().generate_ticker_report(no_trade)

    assert "| NO_TRADE | 1 | BBCA |" in report
    assert "## Executable Stocks\n\nNone." in report
    assert "| **Recommendation** | **NO_TRADE** |" in ticker_report
    assert "| **Model Opinion** | BUY |" in ticker_report


def test_batch_summary_shows_execution_authority_and_diagnostics() -> None:
    result = _mock_result()
    result["regime_context"] = {
        "execution_regime": "SIDEWAYS",
        "execution_regime_reason": "hmm_sideways",
        "trend_regime": {"label": "SIDEWAYS", "confidence": 0.8},
        "volatility_regime": "NORMAL",
    }

    report = MarkdownFormatter().generate_batch_summary([result], "run-123")

    assert "## Execution Regime Authority" in report
    assert "| BBCA | SIDEWAYS | hmm_sideways | SIDEWAYS (80.0%) | NORMAL |" in report


def test_rich_ticker_and_batch_show_canonical_regime() -> None:
    ticker_console = Console(record=True, width=180)
    batch_console = Console(record=True, width=220)
    result = _mock_result()
    result.update(
        {
            "execution_regime": "DEFENSIVE",
            "execution_regime_reason": "rule_based_defensive_override",
            "trend_regime": {"label": "SIDEWAYS", "confidence": 0.9},
            "volatility_regime": "HIGH",
        }
    )

    RichFormatter(console=ticker_console).render_ticker_panel(result)
    RichFormatter(console=batch_console).render_batch_summary([result])
    ticker_output = ticker_console.export_text()
    batch_output = batch_console.export_text()

    assert "Execution Regime" in ticker_output
    assert "DEFENSIVE" in ticker_output
    assert "rule_based_defensive_override" in ticker_output
    assert "Trend (diagnostic)" in ticker_output
    assert "REGIME AUTHORITY" in batch_output
    assert "rule_based_defensive_override" in batch_output


def test_generate_batch_summary_includes_all_ratings_and_counts_total() -> None:
    buy = _mock_result("BUY")
    insufficient = _mock_result("INSUFFICIENT_DATA")
    insufficient["ticker"] = "BACH"
    insufficient["verdict"]["ticker"] = "BACH"

    report = MarkdownFormatter().generate_batch_summary(
        [buy, insufficient],
        "run-123",
    )

    # Scope to the "Overall Results" rating table only — the report also has
    # a separate "Canonical Execution Decisions" table using the same
    # "| LABEL | count | ... |" row shape, which a repo-wide regex would
    # double count.
    overall_results = report.split("## Overall Results", 1)[1].split("##", 1)[0]
    counts = {
        rating: int(count)
        for rating, count in re.findall(
            r"^\| ([A-Z_]+) \| (\d+) \|",
            overall_results,
            flags=re.MULTILINE,
        )
    }

    assert "**Total Stocks**: 2" in report
    assert "| INSUFFICIENT_DATA | 1 | BACH |" in report
    assert counts["INSUFFICIENT_DATA"] == 1
    assert sum(counts.values()) == 2


def test_vote_table_soft_hold_uses_override_note() -> None:
    result = _mock_result("HOLD")
    result["consensus_method"] = "soft_hold"

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| Agent | Position | Confidence | Effective Confidence |" in report
    assert "| Agent | Position | Confidence | Outcome |" not in report
    assert "overridden by soft_hold_rule" in report


def test_generate_ticker_report_shows_breaking_news_section() -> None:
    result = _mock_result()
    result["has_breaking_news"] = True
    result["breaking_news_headlines"] = [
        {
            "title": "BBCA faces sudden regulatory pressure",
            "source": "IDX News",
            "timestamp": "2026-06-01T09:00:00+07:00",
        }
    ]

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "⚠️  BREAKING NEWS" in report
    assert "BBCA faces sudden regulatory pressure — IDX News" in report


def test_generate_ticker_report_suppresses_unverified_fair_value() -> None:
    result = _mock_result()
    result["verdict"]["fair_value"] = None
    result["verdict"]["valuation_gap"] = "unverified"

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| **Fair Value** |" not in report
    assert "| **Gap** | unverified |" in report


def test_preflight_fair_value_status_is_visible_in_markdown_and_rich() -> None:
    result = _mock_result(rating="HOLD")
    result["verdict"].update(
        {
            "fair_value": None,
            "fair_value_low": None,
            "fair_value_high": None,
            "fair_value_status": "NOT_EVALUATED_PREFLIGHT",
        }
    )

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| **Fair Value** | N/A |" in report
    assert (
        "| **Fair Value Status** | NOT_EVALUATED_PREFLIGHT |" in report
    )
    assert "| **Gap** | NOT_EVALUATED_PREFLIGHT |" in report

    console = Console(record=True, width=140)
    RichFormatter(console=console).render_ticker_panel(result)
    output = console.export_text()

    assert "Fair Value Status" in output
    assert "NOT_EVALUATED_PREFLIGHT" in output


def test_generate_ticker_report_shows_fair_value_range_and_risk_flag() -> None:
    result = _mock_result()
    result["verdict"].update(
        {
            "current_price": 108,
            "fair_value": 100,
            "fair_value_base": 100,
            "fair_value_low": 85,
            "fair_value_high": 115,
            "risk_overvalued": False,
        }
    )

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| **Fair Value** | Rp 100 |" in report
    assert "| **Fair Value Range** | Rp 85 - Rp 115 |" in report
    assert "| **Risk Overvalued** | False |" in report
    assert "| **Gap** | -7.4% (SLIGHTLY_OVERVALUED) |" in report
    assert "| **Gap** | -7.4% (OVERVALUED) |" not in report


def test_render_ticker_panel_handles_minimal_result() -> None:
    console = Console(record=True, width=100)
    formatter = RichFormatter(console=console)

    formatter.render_ticker_panel({"ticker": "BBCA"})

    assert "BBCA" in console.export_text()


def test_render_ticker_panel_contains_debate_argument_highlights() -> None:
    console = Console(record=True, width=140)
    formatter = RichFormatter(console=console)

    formatter.render_ticker_panel(_mock_result())

    output = console.export_text()
    assert "KEY DEBATE" in output
    assert "Strong bull case" in output
    assert "Bear case highlights valuation" in output
    assert "Decision Summary" in output
    assert "Agent rationale" in output
    assert "News Sentiment" not in output
    assert "Advocatus" not in output


def test_render_ticker_panel_shows_breaking_news_headlines() -> None:
    console = Console(record=True, width=140)
    formatter = RichFormatter(console=console)
    result = _mock_result()
    result["metadata"]["has_breaking_news"] = True
    result["metadata"]["breaking_news_headlines"] = [
        {
            "title": "BBCA headline risk escalates",
            "source": "Kontan",
            "timestamp": "2026-06-01T09:00:00+07:00",
        }
    ]

    formatter.render_ticker_panel(result)

    output = console.export_text()
    assert "BREAKING NEWS" in output
    assert "BBCA headline risk escalates" in output
    assert "Kontan" in output


def test_argument_highlight_uses_latest_round_and_strips_footer() -> None:
    console = Console(record=True, width=160)
    formatter = RichFormatter(console=console)
    result = _mock_result()
    result["debate_history"] = [
        {
            "role": "bull",
            "content": "Old thesis only.",
            "round": 1,
        },
        {
            "role": "bull",
            "content": (
                "Latest thesis starts with a clear setup. "
                "Bid support at Rp 3,000 and fair value gap 68% improve odds.\n\n"
                "Position: BUY\nAgent Confidence: 0.80"
            ),
            "round": 3,
        },
    ]

    formatter.render_ticker_panel(result)

    output = console.export_text()
    assert "Latest thesis starts" in output
    assert "Rp 3,000" in output
    assert "Old thesis only" not in output
    assert "Position: BUY" not in output


def test_markdown_key_argument_uses_latest_round() -> None:
    result = _mock_result()
    result["debate_history"] = [
        {
            "role": "bull",
            "content": "Latest thesis should win.\n\nPosition: BUY",
            "round": 3,
        },
        {
            "role": "bull",
            "content": "Old thesis should lose.",
            "round": 1,
        },
    ]

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "Latest thesis should win" in report
    assert "Old thesis should lose" not in report


def test_render_batch_summary_handles_empty_results() -> None:
    console = Console(record=True, width=100)
    formatter = RichFormatter(console=console)

    formatter.render_batch_summary([])

    assert "DEBATE RESULTS" in console.export_text()


def test_render_batch_summary_uses_compact_debate_table() -> None:
    console = Console(record=True, width=160)
    formatter = RichFormatter(console=console)

    formatter.render_batch_summary(
        [_mock_result(), {"ticker": "TLKM", "error": "failed"}],
        succeeded=1,
        failed=1,
        duration_seconds=74,
    )

    output = console.export_text()
    assert "DEBATE RESULTS" in output
    assert "Ticker" in output
    assert "Risk Gov" in output
    assert "BBCA" in output
    assert "TLKM" in output
    assert "Succeeded: 1" in output
    assert "Failed: 1" in output
    assert "Duration: 1m 14s" in output
