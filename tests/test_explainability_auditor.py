import copy
import json
from pathlib import Path

from services.explainability_auditor import AuditPacket, ExplainabilityAuditor


def debate_fixture() -> dict:
    return {
        "ticker": "BBCA",
        "verdict": {
            "ticker": "BBCA",
            "rating": "BUY",
            "confidence": 0.72,
            "consensus_method": "voting",
            "consensus_reached": True,
            "consensus_winner": {
                "agent": "bull",
                "position": "BUY",
                "confidence": 0.72,
            },
            "dissenting_agents": ["bear", "sentiment_specialist"],
            "key_risks": ["Foreign outflow could pressure support."],
        },
        "debate_rounds": 2,
        "debate_history": [
            {
                "role": "bull",
                "content": (
                    "Initial bull case rests on valuation support.\n\n"
                    "Position: BUY\n"
                    "Agent Confidence: 0.60"
                ),
                "round": 1,
            },
            {
                "role": "bear",
                "content": (
                    "Bear case sees downside risk from weak momentum.\n\n"
                    "Position: AVOID\n"
                    "Agent Confidence: 0.55"
                ),
                "round": 1,
            },
            {
                "role": "bull",
                "content": (
                    "Final bull case has momentum and valuation support.\n\n"
                    "Position: BUY\n"
                    "Agent Confidence: 0.72"
                ),
                "round": 2,
            },
            {
                "role": "devils_advocate",
                "content": (
                    "Position: BEARISH\n"
                    "Agent Confidence: HIGH\n\n"
                    "Could foreign selling invalidate the setup?"
                ),
                "round": 3,
            },
        ],
        "raw_data_summary": (
            "Ticker: BBCA\n"
            "As Of: 2026-05-12T21:43:03+00:00\n"
            "Current Price: Rp 6,125\n"
            "Fair Value Estimate: Rp 10,474\n"
            "Data Sources: stockbit, gemini, yfinance\n"
            "Missing Fields: none\n"
            'Technical Indicators: {"rsi14":43.7}\n\n'
            'Fundamental Brief: {"brief":"solid bank metrics"}\n\n'
            'Sentiment Brief: ```json {"sentiment":"NEUTRAL"} ```'
        ),
        "metadata": {
            "run_id": "20260513_044236",
            "generated_at": "2026-05-13T04:42:36+07:00",
        },
    }


def test_build_audit_packet_returns_packet_with_ticker_and_rating() -> None:
    packet = ExplainabilityAuditor().build_audit_packet(debate_fixture())

    assert isinstance(packet, AuditPacket)
    assert packet.ticker == "BBCA"
    assert packet.verdict_rating == "BUY"


def test_dissent_rate_calculated_from_two_dissenting_agents() -> None:
    packet = ExplainabilityAuditor().build_audit_packet(debate_fixture())

    assert packet.dissent_rate == 0.4


def test_one_line_summary_is_non_empty_and_contains_ticker() -> None:
    packet = ExplainabilityAuditor().build_audit_packet(debate_fixture())

    assert packet.one_line_summary
    assert "BBCA" in packet.one_line_summary


def test_key_bull_argument_uses_last_bull_message() -> None:
    packet = ExplainabilityAuditor().build_audit_packet(debate_fixture())

    assert packet.key_bull_argument.startswith("Final bull case")


def test_stale_sources_include_sentiment_for_insufficient_data() -> None:
    debate = copy.deepcopy(debate_fixture())
    debate["raw_data_summary"] += "\nSentiment: INSUFFICIENT_DATA"

    packet = ExplainabilityAuditor().build_audit_packet(debate)

    assert "sentiment" in packet.stale_sources


def test_format_report_contains_header_and_ticker() -> None:
    auditor = ExplainabilityAuditor()
    packet = auditor.build_audit_packet(debate_fixture())

    report = auditor.format_report(packet)

    assert "AUDIT REPORT" in report
    assert "BBCA" in report


def test_log_packet_writes_one_jsonl_line(tmp_path: Path) -> None:
    auditor = ExplainabilityAuditor(tmp_path / "audit_log.jsonl")
    packet = auditor.build_audit_packet(debate_fixture())

    auditor.log_packet(packet)

    lines = (tmp_path / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["ticker"] == "BBCA"


def test_audit_from_file_loads_json_and_returns_packet(tmp_path: Path) -> None:
    debate_path = tmp_path / "BBCA_debate.json"
    debate_path.write_text(json.dumps(debate_fixture()), encoding="utf-8")
    auditor = ExplainabilityAuditor(tmp_path / "audit_log.jsonl")

    packet = auditor.audit_from_file(debate_path)

    assert isinstance(packet, AuditPacket)
    assert packet.ticker == "BBCA"
