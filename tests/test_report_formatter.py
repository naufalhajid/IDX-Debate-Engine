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
            "timeframe": "1-3 Months",
            "summary": "BBCA masih menarik dengan risiko terukur.",
            "key_risks": ["Volatilitas IHSG", "Tekanan margin"],
            "key_catalysts": ["Likuiditas kuat", "Rotasi sektor bank"],
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
                "content": "Bull case kuat.\n\nPosition: BUY\nAgent Confidence: 0.70",
            },
            {
                "role": "bear",
                "content": "Bear case menyorot valuasi.\n\nPosition: AVOID\nAgent Confidence: 0.60",
            },
            {
                "role": "devils_advocate",
                "content": "Apa skenario invalidasinya?",
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
                summary="Bull case kuat.",
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
        key_bull_argument="Bull case kuat.",
        key_bear_argument="Bear case menyorot valuasi.",
        devils_advocate_question="Apa skenario invalidasinya?",
        data_freshness_ok=True,
        stale_sources=[],
        missing_fields=[],
        one_line_summary="BBCA BUY dengan keyakinan 70%.",
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

    assert "Siap" in line


def test_risk_governor_reject_line() -> None:
    line = RichFormatter()._risk_governor_line({"status": "reject"})

    assert "Ditolak" in line


def test_risk_governor_conditional_line() -> None:
    line = RichFormatter()._risk_governor_line({"status": "conditional_deployable"})

    assert "Conditional" in line


def test_generate_ticker_report_contains_title_and_ticker(tmp_path) -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result(), _mock_packet())
    path = tmp_path / "latest_report.md"
    path.write_text(report, encoding="utf-8")

    assert "Laporan Analisis" in path.read_text(encoding="utf-8")
    assert "BBCA" in report


def test_generate_ticker_report_buy_contains_catalyst_section() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result("BUY"))

    assert "Katalis Potensial" in report


def test_generate_ticker_report_avoid_skips_catalyst_section() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result("AVOID"))

    assert "Katalis Potensial" not in report


def test_generate_ticker_report_normalizes_strong_buy_rating() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result("STRONG_BUY"))

    assert "| **Rekomendasi** | **BUY** |" in report
    assert "Katalis Potensial" in report


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


def test_markdown_risk_governor_does_not_instantiate_rich_formatter(monkeypatch) -> None:
    def fail_if_called(self, risk):
        raise AssertionError("MarkdownFormatter should not call RichFormatter")

    monkeypatch.setattr(RichFormatter, "_risk_governor_line", fail_if_called)

    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "| **Risk Governor** | Siap dieksekusi |" in report


def test_generate_ticker_report_replaces_advocatus_with_decision_summary() -> None:
    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "Ringkasan & Alasan Pilihan Agent" in report
    assert "Distribusi pilihan agent" in report
    assert "Advocatus Diaboli" not in report


def test_generate_ticker_report_contains_confidence_percent() -> None:
    confidence = _mock_result()["verdict"]["confidence"]
    report = MarkdownFormatter().generate_ticker_report(_mock_result())

    assert "{:.0%}".format(confidence) in report


def test_generate_batch_summary_contains_run_id_and_title() -> None:
    report = MarkdownFormatter().generate_batch_summary([_mock_result()], "run-123")

    assert "run-123" in report
    assert "Ringkasan Analisis Batch" in report


def test_generate_batch_summary_contains_buy_ticker_in_results_table() -> None:
    report = MarkdownFormatter().generate_batch_summary([_mock_result()], "run-123")

    assert "BBCA" in report
    assert "| BUY |" in report


def test_vote_table_soft_hold_uses_override_note() -> None:
    result = _mock_result("HOLD")
    result["consensus_method"] = "soft_hold"

    report = MarkdownFormatter().generate_ticker_report(result)

    assert "| Agent | Posisi | Keyakinan |" in report
    assert "| Agent | Posisi | Keyakinan | Hasil |" not in report
    assert "di-override oleh soft_hold_rule" in report


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
    assert "ARGUMEN KUNCI" in output
    assert "Bull case kuat" in output
    assert "Bear case menyorot valuasi" in output
    assert "Ringkasan Pilihan" in output
    assert "Alasan agent" in output
    assert "Advocatus" not in output


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

    assert "HASIL DEBATE" in console.export_text()


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
    assert "HASIL DEBATE" in output
    assert "Ticker" in output
    assert "Risk Gov" in output
    assert "BBCA" in output
    assert "TLKM" in output
    assert "Berhasil: 1" in output
    assert "Gagal: 1" in output
    assert "Durasi: 1m 14s" in output
