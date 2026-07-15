"""JSONL-backed store for per-run agent observations."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

from core.settings import settings
from utils.logger_config import logger
from utils.ticker import normalize_idx_ticker


DEFAULT_PATH = settings.observations_path


class AgentObservation(BaseModel):
    """Single agent observation captured during a debate run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    ticker: str
    agent: str
    position: str
    confidence: float | None
    summary: str
    round_num: int
    prompt_version: str
    timestamp: str
    evidence: list[str]

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: object) -> str:
        """Keep observation identities canonical before persistence."""
        return normalize_idx_ticker(value)  # type: ignore[arg-type]


class ObservationStore:
    """Append-only JSONL observation store."""

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)

    def append(self, obs: AgentObservation) -> None:
        """Append one observation as a JSON line."""
        validated = AgentObservation.model_validate(obs.model_dump())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(validated.model_dump_json())
            handle.write("\n")

    def query(
        self,
        ticker: str | None = None,
        run_id: str | None = None,
        agent: str | None = None,
    ) -> list[AgentObservation]:
        """Read observations matching all provided filters."""
        ticker_filter = normalize_idx_ticker(ticker) if ticker is not None else None
        observations = self._read_all()
        return [
            obs
            for obs in observations
            if (ticker_filter is None or obs.ticker == ticker_filter)
            and (run_id is None or obs.run_id == run_id)
            and (agent is None or obs.agent == agent)
        ]

    def latest_run_id(self) -> str | None:
        """Return the run ID from the most recently appended record."""
        if not self.path.exists():
            return None
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for line_number in range(len(lines), 0, -1):
            line = lines[line_number - 1]
            if not line.strip():
                continue
            try:
                return AgentObservation.model_validate_json(line).run_id
            except Exception as exc:
                logger.warning(
                    "[ObservationStore] reason_code=invalid_observation_record "
                    "line={} exception_type={} detail={}",
                    line_number,
                    type(exc).__name__,
                    exc,
                )
        return None

    def clear(self) -> None:
        """Remove all stored observations. Intended for tests."""
        if self.path.exists():
            self.path.unlink()

    def _read_all(self) -> list[AgentObservation]:
        if not self.path.exists():
            return []
        observations: list[AgentObservation] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                observations.append(AgentObservation.model_validate_json(line))
            except Exception as exc:
                logger.warning(
                    "[ObservationStore] reason_code=invalid_observation_record "
                    "line={} exception_type={} detail={}",
                    line_number,
                    type(exc).__name__,
                    exc,
                )
        return observations


DEFAULT_STORE = ObservationStore()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query stored agent observations.")
    parser.add_argument("--ticker", help="Filter by ticker")
    parser.add_argument("--run-id", help="Filter by run ID")
    parser.add_argument("--agent", help="Filter by agent name")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_PATH),
        help="Path to observations JSONL file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    store = ObservationStore(args.path)
    observations = store.query(
        ticker=args.ticker,
        run_id=args.run_id,
        agent=args.agent,
    )
    payload = [obs.model_dump() for obs in observations]
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
