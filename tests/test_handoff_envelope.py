from datetime import datetime

from core.handoff_envelope import (
    HandoffEnvelope,
    ProvenanceRecord,
    envelope_from_dict,
    envelope_to_dict,
    make_envelope,
    validate_envelope,
)


def test_make_envelope_auto_fills_created_at_and_defaults() -> None:
    envelope = make_envelope(
        producer="fundamental_scout",
        consumer="synthesizer",
        ticker="BBCA",
        run_id="run-1",
        payload={"roe": 0.21},
        confidence=0.8,
    )

    assert envelope.schema_version == "1.0"
    assert envelope.provenance == []
    assert envelope.errors == []
    assert datetime.fromisoformat(envelope.created_at) is not None


def test_validate_envelope_accepts_valid_envelope() -> None:
    envelope = make_envelope(
        producer="chartist",
        consumer="synthesizer",
        ticker="BBRI",
        run_id="run-1",
        payload={"rsi": 58.2},
    )

    assert validate_envelope(envelope, expected_consumer="synthesizer") == []


def test_validate_envelope_reports_contract_errors() -> None:
    envelope = HandoffEnvelope(
        producer="bear",
        consumer="cio",
        ticker="TLKM",
        run_id="run-1",
        payload={},
        confidence=None,
        provenance=[],
        errors=[],
        created_at="not-a-date",
        schema_version="2.0",
    )

    errors = validate_envelope(envelope, expected_consumer="synthesizer")

    assert "consumer mismatch: expected synthesizer, got cio" in errors
    assert "payload is empty" in errors
    assert "unsupported schema_version: 2.0" in errors
    assert "created_at is not parseable ISO-8601: not-a-date" in errors


def test_envelope_round_trips_through_dict() -> None:
    envelope = make_envelope(
        producer="sentiment",
        consumer="synthesizer",
        ticker="WIIM",
        run_id="run-2",
        payload={"summary": "positive flow"},
        provenance=[
            ProvenanceRecord(
                source="stockbit",
                fetched_at="2026-05-13T10:00:00+07:00",
                freshness_seconds=120,
            )
        ],
        errors=["missing one pinned post"],
    )

    hydrated = envelope_from_dict(envelope_to_dict(envelope))

    assert hydrated == envelope
    assert hydrated.provenance[0].source == "stockbit"
