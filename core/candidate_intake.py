"""Candidate normalization boundary before debate pipeline intake."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class RawCandidate(BaseModel):
    """Permissive wrapper for candidate payloads from upstream pipelines."""

    model_config = ConfigDict(extra="allow")


class NormalizedCandidate(BaseModel):
    """Strict candidate shape required by downstream debate intake."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    price: float
    market_cap: float | None
    sector: str | None
    source: str


def normalize(raw: dict) -> NormalizedCandidate:
    """Normalize one raw candidate into the strict debate intake shape."""
    candidate = RawCandidate.model_validate(raw).model_dump()

    ticker = _first_text(candidate, "ticker", "symbol")
    if ticker is None:
        raise ValueError("Candidate is missing required ticker field.")

    price = _first_number(candidate, "price", "last_price", "close")
    if price is None:
        raise ValueError(f"Candidate {ticker} is missing required price field.")

    return NormalizedCandidate(
        ticker=ticker,
        price=price,
        market_cap=_optional_number(candidate.get("market_cap")),
        sector=_optional_text(candidate.get("sector")),
        source=_optional_text(candidate.get("source")) or "unknown",
    )


def normalize_batch(
    candidates: list[dict],
) -> tuple[list[NormalizedCandidate], list[dict]]:
    """Normalize many candidates, separating invalid payloads without raising."""
    valid: list[NormalizedCandidate] = []
    rejected: list[dict] = []

    for candidate in candidates:
        try:
            valid.append(normalize(candidate))
        except Exception as exc:
            rejected.append({"candidate": candidate, "error": str(exc)})

    return valid, rejected


def _first_text(candidate: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _optional_text(candidate.get(key))
        if value is not None:
            return value
    return None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _first_number(candidate: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _optional_number(candidate.get(key))
        if value is not None:
            return value
    return None


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number
