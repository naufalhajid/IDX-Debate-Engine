"""Standard contract for passing data between agents and pipeline stages."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


SUPPORTED_SCHEMA_VERSIONS = {"1.0"}


class ProvenanceRecord(BaseModel):
    """Source and freshness metadata for data inside a handoff envelope."""

    model_config = ConfigDict(extra="forbid")

    source: str
    fetched_at: str
    freshness_seconds: int | None


class HandoffEnvelope(BaseModel):
    """Typed handoff payload between agents or pipeline stages."""

    model_config = ConfigDict(extra="forbid")

    producer: str
    consumer: str
    ticker: str
    run_id: str
    payload: dict[str, Any]
    confidence: float | None
    provenance: list[ProvenanceRecord] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    created_at: str
    schema_version: str = "1.0"


def make_envelope(
    producer: str,
    consumer: str,
    ticker: str,
    run_id: str,
    payload: dict,
    confidence: float | None = None,
    provenance: list[ProvenanceRecord] | None = None,
    errors: list[str] | None = None,
) -> HandoffEnvelope:
    """Create a handoff envelope with an auto-filled creation timestamp."""
    return HandoffEnvelope(
        producer=producer,
        consumer=consumer,
        ticker=ticker,
        run_id=run_id,
        payload=payload,
        confidence=confidence,
        provenance=provenance or [],
        errors=errors or [],
        created_at=datetime.now(UTC).isoformat(),
        schema_version="1.0",
    )


def validate_envelope(
    envelope: HandoffEnvelope,
    expected_consumer: str,
) -> list[str]:
    """Return validation errors for a stage handoff envelope."""
    errors: list[str] = []
    if envelope.consumer != expected_consumer:
        errors.append(
            f"consumer mismatch: expected {expected_consumer}, got {envelope.consumer}"
        )
    if not envelope.payload:
        errors.append("payload is empty")
    if envelope.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"unsupported schema_version: {envelope.schema_version}")
    if not _is_parseable_iso8601(envelope.created_at):
        errors.append(f"created_at is not parseable ISO-8601: {envelope.created_at}")
    return errors


def envelope_to_dict(envelope: HandoffEnvelope) -> dict:
    """Serialize a handoff envelope to a plain dictionary."""
    return envelope.model_dump()


def envelope_from_dict(data: dict) -> HandoffEnvelope:
    """Hydrate a handoff envelope from a plain dictionary."""
    return HandoffEnvelope.model_validate(data)


def _is_parseable_iso8601(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True
