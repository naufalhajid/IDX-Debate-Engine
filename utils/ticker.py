"""Canonical IDX ticker validation and filesystem containment helpers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
import re
from typing import Any


IDX_TICKER_PATTERN = re.compile(r"^[A-Z]{4}(?:\.JK)?$", flags=re.ASCII)
_FORBIDDEN_PATH_CHARS = frozenset({"/", "\\", ":", "%", "\x00"})


class InvalidIDXTicker(ValueError):
    """Raised when a value is not a canonical four-letter IDX ticker."""


class PathContainmentError(ValueError):
    """Raised when an artifact path resolves outside its requested root."""


def normalize_idx_ticker(value: str, *, keep_suffix: bool = False) -> str:
    """Validate and normalize an IDX ticker to ``BBCA`` (or ``BBCA.JK``).

    Validation is intentionally performed on ASCII input before uppercasing.
    This prevents Unicode case-folding expansions/confusables from becoming a
    seemingly valid filesystem component after normalization.
    """

    if not isinstance(value, str):
        raise InvalidIDXTicker("Invalid IDX ticker: value must be a string.")
    raw = value.strip()
    if not raw:
        raise InvalidIDXTicker("Invalid IDX ticker: value must not be empty.")
    if not raw.isascii():
        raise InvalidIDXTicker(
            "Invalid IDX ticker: value must contain ASCII letters only."
        )
    if any(character in raw for character in _FORBIDDEN_PATH_CHARS):
        raise InvalidIDXTicker(
            "Invalid IDX ticker: value contains a forbidden path character."
        )

    normalized = raw.upper()
    if IDX_TICKER_PATTERN.fullmatch(normalized) is None:
        raise InvalidIDXTicker(
            "Invalid IDX ticker: expected four ASCII letters, optionally followed by .JK."
        )
    base = normalized.removesuffix(".JK")
    return f"{base}.JK" if keep_suffix else base


def normalize_idx_tickers(
    values: Iterable[str],
    *,
    require_nonempty: bool = True,
) -> list[str]:
    """Normalize and de-duplicate tickers while preserving their input order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        ticker = normalize_idx_ticker(value)
        if ticker not in seen:
            seen.add(ticker)
            normalized.append(ticker)
    if require_nonempty and not normalized:
        raise InvalidIDXTicker("Invalid IDX ticker list: at least one is required.")
    return normalized


def to_yfinance_symbol(value: str) -> str:
    """Return the canonical Yahoo Finance symbol for one IDX ticker."""

    return normalize_idx_ticker(value, keep_suffix=True)


def canonicalize_result_identity(
    payload: Mapping[str, Any],
    *,
    expected_ticker: str | None = None,
) -> dict[str, Any]:
    """Validate one persisted result has a single canonical ticker identity.

    A result may repeat its ticker in nested decision payloads.  Every present
    identity must resolve to the same four-letter IDX symbol; contradictions are
    rejected instead of being silently relabelled as another stock.
    """

    if not isinstance(payload, Mapping):
        raise TypeError("result payload must be a mapping")

    identities: list[tuple[str, str]] = []

    def collect(label: str, value: Any) -> None:
        if value in (None, ""):
            return
        identities.append((label, normalize_idx_ticker(value)))

    collect("expected_ticker", expected_ticker)
    collect("ticker", payload.get("ticker"))
    nested_keys = (
        "verdict",
        "final_verdict",
        "risk_governor",
        "execution_decision",
    )
    for key in nested_keys:
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            collect(f"{key}.ticker", nested.get("ticker"))

    if not identities:
        raise InvalidIDXTicker("Invalid result identity: ticker is required.")

    canonical = identities[0][1]
    mismatches = [
        f"{label}={ticker}"
        for label, ticker in identities
        if ticker != canonical
    ]
    if mismatches:
        raise InvalidIDXTicker(
            "Conflicting result ticker identities: " + ", ".join(mismatches)
        )

    normalized = dict(payload)
    normalized["ticker"] = canonical
    for key in nested_keys:
        nested = payload.get(key)
        if not isinstance(nested, Mapping):
            continue
        if key in {"verdict", "final_verdict"} or nested.get("ticker") not in (
            None,
            "",
        ):
            normalized[key] = {**nested, "ticker": canonical}
    return normalized


def resolve_within_root(root: Path | str, *parts: Path | str) -> Path:
    """Resolve a descendant path and reject traversal, absolute overrides, and links.

    The returned resolved path must be used for I/O. Re-resolving both sides
    makes this a defense against existing symlinks/junctions as well as ``..``.
    """

    safe_parts: list[Path] = []
    for part in parts:
        candidate_part = Path(part)
        if (
            candidate_part.is_absolute()
            or bool(candidate_part.anchor)
            or ".." in candidate_part.parts
        ):
            raise PathContainmentError(
                "Artifact path contains an absolute or parent component."
            )
        safe_parts.append(candidate_part)

    resolved_root = Path(root).resolve()
    target = resolved_root.joinpath(*safe_parts).resolve()
    if not target.is_relative_to(resolved_root):
        raise PathContainmentError("Artifact path escapes the requested root.")
    return target


__all__ = [
    "IDX_TICKER_PATTERN",
    "InvalidIDXTicker",
    "PathContainmentError",
    "canonicalize_result_identity",
    "normalize_idx_ticker",
    "normalize_idx_tickers",
    "resolve_within_root",
    "to_yfinance_symbol",
]
