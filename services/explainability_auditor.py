"""Read-only explainability audit packets for persisted debate verdicts."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from core.settings import settings
from utils.logger_config import logger
from utils.ticker import InvalidIDXTicker, normalize_idx_ticker, resolve_within_root


DEFAULT_PATH = settings.audit_log_path

AGENT_ROLES = {
    "bull",
    "bear",
    "devils_advocate",
    "fundamental_scout",
    "chartist",
    "sentiment_specialist",
}

CONSENSUS_AGENT_ROLES = {
    "chartist",
    "sentiment_specialist",
    "bull",
    "bear",
}


def _format_optional_float(value: float | None) -> str:
    return "?" if value is None else f"{value:.2f}"


class AgentVoteSummary(BaseModel):
    """Compact per-agent vote extracted from the debate transcript."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    position: str
    confidence: float | None
    calibration_weight: float | None = None
    effective_confidence: float | None = None
    round_num: int
    supporting_winner: bool
    summary: str


class EvidenceItem(BaseModel):
    """Evidence section copied from the persisted debate raw-data summary."""

    model_config = ConfigDict(extra="forbid")

    category: str
    content: str
    source: str
    is_stale: bool
    freshness_note: str | None


class AuditPacket(BaseModel):
    """Human-readable audit payload for a CIO verdict."""

    model_config = ConfigDict(extra="forbid")

    ticker: str
    run_id: str
    generated_at: str

    verdict_rating: str
    verdict_confidence: float
    consensus_method: str
    consensus_reached: bool
    debate_rounds: int

    winner_agent: str | None
    winner_position: str | None
    winner_confidence: float | None
    winner_raw_confidence: float | None = None
    winner_effective_confidence: float | None = None
    dissenting_agents: list[str]
    dissent_rate: float

    agent_votes: list[AgentVoteSummary]
    evidence_used: list[EvidenceItem]

    key_bull_argument: str
    key_bear_argument: str
    devils_advocate_question: str

    data_freshness_ok: bool
    stale_sources: list[str]
    missing_fields: list[str]

    one_line_summary: str


class ExplainabilityAuditor:
    """Build and persist explainability reports from debate JSON artifacts."""

    _POSITION_RE = re.compile(r"(?im)^\s*Position:\s*([A-Z_ -]+)")
    _CONFIDENCE_RE = re.compile(
        r"(?im)^\s*Agent\s+Confidence:\s*([0-9]+(?:\.[0-9]+)?|[A-Z_]+)"
    )

    def __init__(self, storage_path: str | Path = DEFAULT_PATH) -> None:
        self.storage_path = Path(storage_path)
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def build_audit_packet(self, debate_json: dict[str, Any]) -> AuditPacket:
        """Build a structured audit packet from an already-parsed debate JSON."""
        verdict = self._as_mapping(debate_json.get("verdict"))
        metadata = self._as_mapping(debate_json.get("metadata"))

        ticker = str(debate_json.get("ticker") or verdict.get("ticker") or "UNKNOWN")
        rating = str(verdict.get("rating") or "UNKNOWN")
        confidence = self._float_or_default(verdict.get("confidence"), 0.0)
        consensus_method = str(verdict.get("consensus_method") or "unknown")
        consensus_reached = bool(verdict.get("consensus_reached", False))
        debate_rounds = self._int_or_default(debate_json.get("debate_rounds"), 0)
        dissenting_agents = [
            agent
            for agent in self._string_list(verdict.get("dissenting_agents"))
            if self._canonical_agent(agent) in CONSENSUS_AGENT_ROLES
        ]

        # FIX: ISSUE 2 — Preserve effective calibrated confidence in audit packets.
        agent_votes = self._parse_explicit_agent_votes(
            debate_json.get("agent_votes"),
            verdict_rating=rating,
        ) or self._parse_agent_votes(
            debate_json.get("debate_history"),
            verdict_rating=rating,
        )
        explicit_winner = self._extract_explicit_winner(debate_json, verdict)
        if (
            consensus_method == "confidence_winner"
            and self._canonical_agent(explicit_winner.get("agent"))
            not in CONSENSUS_AGENT_ROLES
        ):
            explicit_winner = {
                "agent": None,
                "position": None,
                "confidence": None,
                "effective_confidence": None,
            }
        if consensus_method == "soft_hold":
            winner_agent = "soft_hold_rule"
            winner_position = "HOLD"
            winner_confidence = confidence
            winner_raw_confidence = confidence
            winner_effective_confidence = confidence
        elif consensus_method == "confidence_winner":
            winner_agent = explicit_winner.get("agent") or self._infer_summary_winner(
                agent_votes,
                rating,
                consensus_method,
            )
            winner_position = explicit_winner.get(
                "position"
            ) or self._canonical_position(rating)
            winner_confidence = explicit_winner.get("confidence") or confidence
            winner_raw_confidence = explicit_winner.get("confidence") or confidence
            winner_effective_confidence = explicit_winner.get("effective_confidence")
        else:
            winner_agent = explicit_winner.get("agent")
            winner_position = explicit_winner.get("position")
            winner_confidence = explicit_winner.get("confidence")
            winner_raw_confidence = explicit_winner.get("confidence")
            winner_effective_confidence = explicit_winner.get("effective_confidence")

        raw_data_summary = str(debate_json.get("raw_data_summary") or "")
        evidence_used = self._extract_evidence(raw_data_summary)
        key_bull_argument = self._last_role_content(
            debate_json.get("debate_history"),
            "bull",
            300,
        )
        key_bear_argument = self._last_role_content(
            debate_json.get("debate_history"),
            "bear",
            300,
        )
        devils_advocate_question = self._last_role_content(
            debate_json.get("debate_history"),
            "devils_advocate",
            300,
        )
        stale_sources = self._extract_stale_sources(raw_data_summary)
        missing_fields = self._extract_missing_fields(raw_data_summary)

        run_id = str(
            metadata.get("run_id")
            or metadata.get("run_timestamp")
            or metadata.get("batch_timestamp")
            or "unknown"
        )
        first_risk = self._first_non_empty(
            self._string_list(verdict.get("key_risks"))
            + [str(verdict.get("critical_risk_factor") or "")]
        )
        one_line_summary = self._build_one_line_summary(
            ticker=ticker,
            rating=rating,
            winner=winner_agent
            or self._infer_summary_winner(agent_votes, rating, consensus_method),
            method=consensus_method,
            confidence=confidence,
            rounds=debate_rounds,
            dissent_count=len(dissenting_agents),
            first_risk=first_risk,
        )

        return AuditPacket(
            ticker=ticker,
            run_id=run_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
            verdict_rating=rating,
            verdict_confidence=confidence,
            consensus_method=consensus_method,
            consensus_reached=consensus_reached,
            debate_rounds=debate_rounds,
            winner_agent=winner_agent,
            winner_position=winner_position,
            winner_confidence=winner_confidence,
            winner_raw_confidence=winner_raw_confidence,
            winner_effective_confidence=winner_effective_confidence,
            dissenting_agents=dissenting_agents,
            dissent_rate=min(
                len(set(dissenting_agents)) / len(CONSENSUS_AGENT_ROLES),
                1.0,
            ),
            agent_votes=agent_votes,
            evidence_used=evidence_used,
            key_bull_argument=key_bull_argument,
            key_bear_argument=key_bear_argument,
            devils_advocate_question=devils_advocate_question,
            data_freshness_ok=not stale_sources,
            stale_sources=stale_sources,
            missing_fields=missing_fields,
            one_line_summary=one_line_summary,
        )

    def log_packet(self, packet: AuditPacket) -> None:
        """Append an audit packet to JSONL storage without crashing callers."""
        try:
            with self.storage_path.open("a", encoding="utf-8") as handle:
                handle.write(packet.model_dump_json() + "\n")
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
            return

    def audit_from_file(self, debate_json_path: str | Path) -> AuditPacket:
        """Load a debate JSON file, build an audit packet, log it, and return it."""
        with Path(debate_json_path).open(encoding="utf-8") as handle:
            debate_json = json.load(handle)
        packet = self.build_audit_packet(debate_json)
        self.log_packet(packet)
        return packet

    def format_report(self, packet: AuditPacket) -> str:
        """Render an audit packet as a human-readable text report."""
        agent_lines = [self._format_agent_vote(vote) for vote in packet.agent_votes]
        if not agent_lines:
            agent_lines = ["  none"]

        stale_sources = self._format_list(packet.stale_sources)
        missing_fields = self._format_list(packet.missing_fields)
        consensus = "yes" if packet.consensus_reached else "no"

        return "\n".join(
            [
                "╔══════════════════════════════════════════╗",
                f"║  AUDIT REPORT: {packet.ticker} — {packet.verdict_rating:<10} ║",
                "╚══════════════════════════════════════════╝",
                "",
                f"Summary : {packet.one_line_summary}",
                f"Run ID  : {packet.run_id}",
                f"Generated: {packet.generated_at}",
                "",
                "VERDICT",
                "───────",
                "Rating     : "
                f"{packet.verdict_rating} "
                f"(confidence: {packet.verdict_confidence:.0%})",
                f"Method     : {packet.consensus_method}",
                (
                    "Winner    : "
                    f"{packet.winner_agent or 'unknown'} "
                    f"(raw={_format_optional_float(packet.winner_raw_confidence)}, "
                    f"effective={_format_optional_float(packet.winner_effective_confidence)})"
                    if packet.consensus_method == "confidence_winner"
                    else f"Winner    : {packet.winner_agent or 'unknown'}"
                ),
                f"Consensus  : {consensus}",
                f"Rounds     : {packet.debate_rounds}",
                "Dissent    : "
                f"{packet.dissent_rate:.0%} "
                f"({len(packet.dissenting_agents)} of 5 agents)",
                "",
                "AGENT VOTES",
                "───────────",
                *agent_lines,
                "",
                "KEY ARGUMENTS",
                "─────────────",
                f"Bull  : {packet.key_bull_argument}",
                f"Bear  : {packet.key_bear_argument}",
                f"Devil : {packet.devils_advocate_question}",
                "",
                "DATA QUALITY",
                "────────────",
                f"Freshness OK : {packet.data_freshness_ok}",
                f"Stale sources: {stale_sources}",
                f"Missing      : {missing_fields}",
            ]
        )

    @classmethod
    def _parse_explicit_agent_votes(
        cls,
        raw_votes: Any,
        verdict_rating: str,
    ) -> list[AgentVoteSummary]:
        votes: list[AgentVoteSummary] = []
        if not isinstance(raw_votes, list):
            return votes
        winner_position = cls._canonical_position(verdict_rating)
        for raw_vote in raw_votes:
            if not isinstance(raw_vote, dict):
                continue
            agent = str(raw_vote.get("agent") or "")
            if agent not in AGENT_ROLES:
                continue
            position = str(raw_vote.get("position") or "UNKNOWN")
            votes.append(
                AgentVoteSummary(
                    agent=agent,
                    position=position,
                    confidence=cls._float_or_none(raw_vote.get("confidence")),
                    calibration_weight=cls._float_or_none(
                        raw_vote.get("calibration_weight")
                    ),
                    effective_confidence=cls._float_or_none(
                        raw_vote.get("effective_confidence")
                    ),
                    round_num=cls._int_or_default(
                        raw_vote.get("round_num", raw_vote.get("round")),
                        0,
                    ),
                    supporting_winner=(
                        cls._canonical_position(position) == winner_position
                    ),
                    summary=str(raw_vote.get("summary") or "")[:200],
                )
            )
        return votes

    @classmethod
    def _parse_agent_votes(
        cls,
        debate_history: Any,
        verdict_rating: str,
    ) -> list[AgentVoteSummary]:
        votes: list[AgentVoteSummary] = []
        if not isinstance(debate_history, list):
            return votes

        winner_position = cls._canonical_position(verdict_rating)
        for raw_message in debate_history:
            if not isinstance(raw_message, dict):
                continue
            role = str(raw_message.get("role") or "")
            if role not in AGENT_ROLES:
                continue
            content = str(raw_message.get("content") or "")
            position = cls._extract_position(content)
            confidence = cls._extract_confidence(content)
            votes.append(
                AgentVoteSummary(
                    agent=role,
                    position=position,
                    confidence=confidence,
                    round_num=cls._int_or_default(
                        raw_message.get("round_num", raw_message.get("round")),
                        0,
                    ),
                    supporting_winner=(
                        cls._canonical_position(position) == winner_position
                    ),
                    summary=content.strip()[:200],
                )
            )
        return votes

    @classmethod
    def _extract_position(cls, content: str) -> str:
        matches = cls._POSITION_RE.findall(content)
        if not matches:
            return "UNKNOWN"
        return matches[-1].strip().upper().replace(" ", "_")

    @classmethod
    def _extract_confidence(cls, content: str) -> float | None:
        matches = cls._CONFIDENCE_RE.findall(content)
        for raw_value in reversed(matches):
            try:
                value = float(raw_value)
            except ValueError:
                continue
            if value > 1:
                value = value / 100
            return max(0.0, min(value, 1.0))
        return None

    @classmethod
    def _extract_explicit_winner(
        cls,
        debate_json: dict[str, Any],
        verdict: dict[str, Any],
    ) -> dict[str, Any]:
        raw_winner = verdict.get("consensus_winner")
        if raw_winner is None:
            raw_winner = debate_json.get("consensus_winner")
        if isinstance(raw_winner, dict):
            return {
                "agent": cls._optional_string(raw_winner.get("agent")),
                "position": cls._optional_string(raw_winner.get("position")),
                "confidence": cls._float_or_none(raw_winner.get("confidence")),
                "effective_confidence": cls._float_or_none(
                    raw_winner.get("effective_confidence")
                ),
            }
        if raw_winner:
            return {
                "agent": str(raw_winner),
                "position": None,
                "confidence": None,
                "effective_confidence": None,
            }
        return {
            "agent": None,
            "position": None,
            "confidence": None,
            "effective_confidence": None,
        }

    @classmethod
    def _extract_evidence(cls, raw_data_summary: str) -> list[EvidenceItem]:
        source = cls._extract_line_value(raw_data_summary, "Data Sources") or "unknown"
        evidence: list[EvidenceItem] = []
        line_specs = [
            ("fair_value", "Fair Value Estimate"),
            ("price", "Current Price"),
        ]
        for category, label in line_specs:
            value = cls._extract_line_value(raw_data_summary, label)
            if value:
                evidence.append(
                    EvidenceItem(
                        category=category,
                        content=f"{label}: {value}",
                        source=source,
                        is_stale=False,
                        freshness_note=None,
                    )
                )

        block_specs = [
            ("technical", "Technical Indicators"),
            ("fundamental", "Fundamental Brief"),
            ("sentiment", "Sentiment Brief"),
        ]
        for category, label in block_specs:
            content = cls._extract_block(raw_data_summary, label)
            if content:
                evidence.append(
                    EvidenceItem(
                        category=category,
                        content=f"{label}: {content}",
                        source=source,
                        is_stale=False,
                        freshness_note=None,
                    )
                )
        return evidence

    @staticmethod
    def _extract_line_value(text: str, label: str) -> str | None:
        match = re.search(rf"(?im)^\s*{re.escape(label)}:\s*(.+?)\s*$", text)
        if not match:
            return None
        return match.group(1).strip()

    @staticmethod
    def _extract_block(text: str, label: str) -> str | None:
        labels = [
            "Technical Indicators",
            "Fundamental Brief",
            "Sentiment Brief",
        ]
        next_labels = "|".join(re.escape(item) for item in labels if item != label)
        pattern = rf"(?ims)^\s*{re.escape(label)}:\s*(.*?)(?=\n\n(?:{next_labels}):|\Z)"
        match = re.search(pattern, text)
        if not match:
            return None
        return match.group(1).strip()

    @classmethod
    def _last_role_content(
        cls,
        debate_history: Any,
        role: str,
        limit: int,
    ) -> str:
        if not isinstance(debate_history, list):
            return ""
        contents = [
            str(message.get("content") or "").strip()
            for message in debate_history
            if isinstance(message, dict) and message.get("role") == role
        ]
        return contents[-1][:limit] if contents else ""

    @staticmethod
    def _extract_stale_sources(raw_data_summary: str) -> list[str]:
        if re.search(r"INSUFFICIENT_DATA|Unavailable", raw_data_summary, re.I):
            return ["sentiment"]
        return []

    @classmethod
    def _extract_missing_fields(cls, raw_data_summary: str) -> list[str]:
        raw_value = cls._extract_line_value(raw_data_summary, "Missing Fields")
        if not raw_value or raw_value.strip().lower() == "none":
            return []
        cleaned = raw_value.strip().strip("[]")
        fields = [
            part.strip().strip("'\"")
            for part in re.split(r"[,;]", cleaned)
            if part.strip().strip("'\"")
        ]
        return fields

    @classmethod
    def _build_one_line_summary(
        cls,
        *,
        ticker: str,
        rating: str,
        winner: str | None,
        method: str,
        confidence: float,
        rounds: int,
        dissent_count: int,
        first_risk: str,
    ) -> str:
        method_label = cls._display_method(method)
        if winner == "soft_hold_rule":
            summary = (
                f"{ticker}→{rating}: soft_hold_rule applied "
                f"({confidence:.2f}) after {rounds} round(s). "
                f"{dissent_count} agent(s) dissented."
            )
        else:
            winner_label = winner or "unknown"
            summary = (
                f"{ticker}→{rating}: {winner_label} won by {method_label} "
                f"({confidence:.2f}) after {rounds} round(s). "
                f"{dissent_count} agent(s) dissented."
            )
        if first_risk:
            summary = f"{summary} {first_risk}"
        return summary

    @classmethod
    def _infer_summary_winner(
        cls,
        agent_votes: list[AgentVoteSummary],
        rating: str,
        method: str,
    ) -> str | None:
        if method != "confidence_winner":
            return None
        target = cls._canonical_position(rating)
        supporting_votes = [
            vote
            for vote in agent_votes
            if vote.confidence is not None
            and vote.agent in CONSENSUS_AGENT_ROLES
            and cls._canonical_position(vote.position) == target
        ]
        if not supporting_votes:
            return None
        return max(supporting_votes, key=lambda vote: vote.confidence or 0.0).agent

    @staticmethod
    def _canonical_position(value: str | None) -> str:
        token = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        if token in {"STRONG_BUY", "BUY", "BULLISH", "ACCUMULATE"}:
            return "BUY"
        if token in {"SELL", "AVOID", "BEARISH", "DISTRIBUTE"}:
            return "AVOID"
        if token in {"HOLD", "NEUTRAL", "WAIT", "WAIT_AND_SEE"}:
            return "HOLD"
        return token or "UNKNOWN"

    @staticmethod
    def _canonical_agent(value: Any) -> str:
        return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")

    @staticmethod
    def _format_agent_vote(vote: AgentVoteSummary) -> str:
        confidence = "?" if vote.confidence is None else f"{vote.confidence:.2f}"
        effective = (
            "?"
            if vote.effective_confidence is None
            else f"{vote.effective_confidence:.2f}"
        )
        support_mark = "✅" if vote.supporting_winner else "❌"
        return (
            f"  {vote.agent:<22} {vote.position:<8} "
            f"conf={confidence:>5} eff={effective:>5}  {support_mark}"
        )

    @staticmethod
    def _format_list(values: list[str]) -> str:
        return ", ".join(values) if values else "none"

    @staticmethod
    def _display_agent(value: str | None) -> str:
        if not value:
            return "Unknown"
        return str(value).replace("_", " ").title()

    @staticmethod
    def _display_method(value: str) -> str:
        if value == "confidence_winner":
            return "confidence"
        return value.replace("_", " ")

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item)]
        if value:
            return [str(value)]
        return []

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value)
        return text if text else None

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _float_or_default(cls, value: Any, default: float) -> float:
        parsed = cls._float_or_none(value)
        return default if parsed is None else parsed

    @staticmethod
    def _int_or_default(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _first_non_empty(values: list[str]) -> str:
        for value in values:
            cleaned = value.strip()
            if cleaned:
                return cleaned
        return ""


DEFAULT_AUDITOR = ExplainabilityAuditor()


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a debate audit report.")
    parser.add_argument("--ticker", required=True, help="IDX ticker, e.g. BBCA")
    args = parser.parse_args()

    try:
        ticker = normalize_idx_ticker(args.ticker)
    except InvalidIDXTicker as exc:
        parser.error(str(exc))
    debate_path = resolve_within_root(
        settings.debates_dir,
        ticker,
        "latest_debate.json",
    )
    packet = DEFAULT_AUDITOR.audit_from_file(debate_path)
    print(DEFAULT_AUDITOR.format_report(packet))


if __name__ == "__main__":
    main()
