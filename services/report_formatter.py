"""Human-friendly Rich and Markdown formatters for debate results."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from services.explainability_auditor import AuditPacket
from utils.logger_config import logger


_AGENT_ORDER = (
    "bull",
    "bear",
    "chartist",
    "fundamental_scout",
    "devils_advocate",
)

_AGENT_LABELS = {
    "bull": "Bull",
    "bear": "Bear",
    "chartist": "Chartist",
    "fundamental_scout": "Fundamental Scout",
    "devils_advocate": "Devil's Advocate",
    "sentiment_specialist": "Sentiment Specialist",
}

_METHOD_LABELS = {
    "voting": "Voting mayoritas",
    "confidence_winner": "Pemenang confidence",
    "soft_hold": "Aturan soft hold",
}


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_or_empty(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _canonical_position(value: Any) -> str:
    token = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if token in {"STRONG_BUY", "BUY", "BULLISH", "ACCUMULATE"}:
        return "BUY"
    if token in {"SELL", "AVOID", "BEARISH", "DISTRIBUTE"}:
        return "AVOID"
    if token in {"HOLD", "NEUTRAL", "WAIT", "WAIT_AND_SEE"}:
        return "HOLD"
    return token or "UNKNOWN"


def _is_devils_advocate_agent(value: Any) -> bool:
    token = _canonical(value)
    return "devil" in token or "devils_advocate" in token


def _agent_label(agent: Any) -> str:
    agent_key = _canonical(agent)
    if agent_key in _AGENT_LABELS:
        return _AGENT_LABELS[agent_key]
    text = str(agent or "Unknown").strip().replace("_", " ")
    return text.title() if text else "Unknown"


def _winner_missing(value: Any) -> bool:
    token = _canonical(value)
    return token in {"", "none", "null", "unknown", "n/a", "data_tidak_tersedia"}


def _soft_hold_label() -> str:
    return "Soft Hold Rule\n(tidak ada konsensus)"


def _is_soft_hold_winner(method: Any, winner: Any = None) -> bool:
    try:
        method_token = _canonical(method)
        winner_token = _canonical(winner)
        return "soft_hold" in method_token or winner_token in {
            "soft_rule",
            "soft_hold_rule",
        }
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return False


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    cleaned = re.sub(r"[^\d,.\-]", "", text)
    if not cleaned or cleaned in {"-", ".", ","}:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        parts = cleaned.split(",")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            cleaned = cleaned.replace(",", "")
        else:
            cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts[-1]) == 3 and all(part.isdigit() for part in parts):
            cleaned = cleaned.replace(".", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _confidence(value: Any) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(number, 1.0))


def _money(value: Any, *, include_prefix: bool = True) -> str:
    number = _safe_float(value)
    if number is None or number <= 0:
        return "N/A"
    formatted = f"{number:,.0f}"
    return f"Rp {formatted}" if include_prefix else formatted


def _pct(value: Any) -> str:
    number = _confidence(value)
    return "N/A" if number is None else f"{number:.0%}"


def _signed_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.1f}%"


def _ratio(value: Any) -> str:
    number = _safe_float(value)
    return "N/A" if number is None else f"{number:.2f}x"


def _method_indonesian(value: Any) -> str:
    method = str(value or "unknown")
    return _METHOD_LABELS.get(method, method)


def _yes_no(value: Any) -> str:
    return "Ya" if bool(value) else "Tidak"


def _now_wib() -> datetime:
    try:
        return datetime.now(ZoneInfo("Asia/Jakarta"))
    except Exception as exc:
        logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
        return datetime.now().astimezone()


def _date_wib() -> str:
    return _now_wib().strftime("%Y-%m-%d %H:%M:%S WIB")


def _verdict(result: dict[str, Any]) -> dict[str, Any]:
    return _dict_or_empty(result.get("verdict"))


def _ticker(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    return str(
        result.get("ticker")
        or _verdict(result).get("ticker")
        or (packet.ticker if packet else None)
        or "UNKNOWN"
    ).upper()


def _rating(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    verdict = _verdict(result)
    return str(
        verdict.get("rating")
        or result.get("rating")
        or (packet.verdict_rating if packet else None)
        or "UNKNOWN"
    ).upper()


def _entry_bounds(verdict: dict[str, Any]) -> tuple[float | None, float | None]:
    low = _safe_float(verdict.get("entry_low") or verdict.get("entry_price_low"))
    high = _safe_float(verdict.get("entry_high") or verdict.get("entry_price_high"))
    if low is not None and high is not None:
        return low, high
    raw = verdict.get("entry_price_range")
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return _safe_float(raw[0]), _safe_float(raw[1])
    text = str(raw or "")
    parts = [part for part in re.split(r"\s*(?:-|–|—|to|s/d)\s*", text) if part]
    if len(parts) >= 2:
        return _safe_float(parts[0]), _safe_float(parts[1])
    parsed = _safe_float(text)
    return parsed, parsed


def _price_diff_pct(fair_value: Any, current_price: Any) -> float | None:
    fv = _safe_float(fair_value)
    price = _safe_float(current_price)
    if fv is None or fv <= 0 or price is None or price <= 0:
        return None
    return ((fv - price) / price) * 100


def _move_pct(target: Any, current_price: Any) -> float | None:
    target_number = _safe_float(target)
    price = _safe_float(current_price)
    if target_number is None or target_number <= 0 or price is None or price <= 0:
        return None
    return ((target_number - price) / price) * 100


def _downside_pct(stop_loss: Any, current_price: Any) -> float | None:
    stop_number = _safe_float(stop_loss)
    price = _safe_float(current_price)
    if stop_number is None or stop_number <= 0 or price is None or price <= 0:
        return None
    return ((price - stop_number) / price) * 100


def _valuation_status(fair_value: Any, current_price: Any) -> str:
    fv = _safe_float(fair_value)
    price = _safe_float(current_price)
    if fv is None or fv <= 0 or price is None or price <= 0:
        return "N/A"
    if fv > price:
        return "UNDERVALUED"
    if fv < price:
        return "OVERVALUED"
    return "FAIR VALUE"


def _short_text(value: Any, limit: int = 60) -> str:
    text = str(value or "").strip()
    if not text:
        return "Data tidak tersedia"
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _get_news(result: dict[str, Any]) -> tuple[str, float]:
    metadata = _dict_or_empty(result.get("metadata"))
    sentiment = str(
        result.get("news_sentiment")
        or metadata.get("news_overall_sentiment")
        or metadata.get("news_sentiment")
        or "tidak tersedia"
    )
    adjustment = _safe_float(
        result.get("news_confidence_adjustment")
        or metadata.get("news_confidence_adjustment")
        or metadata.get("news_adjustment")
    )
    return sentiment, adjustment if adjustment is not None else 0.0


def _generated_at(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    metadata = _dict_or_empty(result.get("metadata"))
    raw = (
        metadata.get("generated_at")
        or metadata.get("run_timestamp")
        or metadata.get("batch_timestamp")
        or (packet.generated_at if packet else None)
    )
    if not raw:
        return _date_wib()
    text = str(raw)
    return text if "WIB" in text.upper() else f"{text} WIB"


def _run_id(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    metadata = _dict_or_empty(result.get("metadata"))
    for raw in (
        metadata.get("run_id"),
        metadata.get("run_timestamp"),
        metadata.get("batch_timestamp"),
        packet.run_id if packet else None,
    ):
        text = str(raw or "").strip()
        if text and text.lower() != "unknown":
            return text
    return "unknown"


def _risk(result: dict[str, Any]) -> dict[str, Any]:
    return _dict_or_empty(result.get("risk_governor"))


def _key_risks(result: dict[str, Any]) -> list[str]:
    verdict = _verdict(result)
    risks = _list_or_empty(verdict.get("key_risks"))
    if not risks and verdict.get("critical_risk_factor"):
        risks = [verdict["critical_risk_factor"]]
    return [str(item).strip() for item in risks if str(item).strip()]


def _catalysts(result: dict[str, Any]) -> list[str]:
    verdict = _verdict(result)
    return [
        str(item).strip()
        for item in _list_or_empty(verdict.get("key_catalysts"))
        if str(item).strip()
    ]


def _raw_history_argument(result: dict[str, Any], role: str, limit: int = 500) -> str:
    messages = []
    for raw in _list_or_empty(result.get("debate_history")):
        if not isinstance(raw, dict):
            continue
        if _canonical(raw.get("role")) == role:
            messages.append(str(raw.get("content") or "").strip())
    text = messages[-1] if messages else ""
    if not text:
        return "Data tidak tersedia"
    return text if len(text) <= limit else text[: limit - 3] + "..."


_ARGUMENT_NUMBER_RE = re.compile(r"(?:Rp\s*)?\d[\d.,]*(?:\s*(?:%|x))?", re.IGNORECASE)


def _history_round(raw: dict[str, Any]) -> int:
    value = raw.get("round")
    if value is None:
        value = raw.get("round_num")
    number = _safe_float(value)
    return -1 if number is None else int(number)


def _clean_argument_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.split(
        r"\n\s*(?:Position|Agent Confidence)\s*:",
        text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    return re.sub(r"\s+", " ", text).strip()


def _argument_sentences(value: Any) -> list[str]:
    text = _clean_argument_text(value)
    if not text:
        return []
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", text)
        if item.strip()
    ]


def _summarize_argument_text(value: Any, limit: int = 220) -> str:
    sentences = _argument_sentences(value)
    if not sentences:
        return "Data tidak tersedia"

    selected: list[str] = [sentences[0]]
    for sentence in sentences[1:]:
        if len(selected) >= 3:
            break
        if _ARGUMENT_NUMBER_RE.search(sentence):
            selected.append(sentence)
    for sentence in sentences[1:]:
        if len(selected) >= 2:
            break
        if sentence not in selected:
            selected.append(sentence)

    summary = " ".join(selected)
    return summary if len(summary) <= limit else summary[: limit - 3].rstrip() + "..."


def _latest_history_argument(result: dict[str, Any], role: str) -> str:
    target = _canonical(role)
    matches: list[tuple[int, str]] = []
    for raw in _list_or_empty(result.get("debate_history")):
        if not isinstance(raw, dict):
            continue
        role_key = _canonical(raw.get("role"))
        if target == "devils_advocate":
            is_match = _is_devils_advocate_agent(role_key)
        else:
            is_match = role_key == target
        if is_match:
            matches.append((_history_round(raw), str(raw.get("content") or "")))
    if not matches:
        return ""
    matches.sort(key=lambda item: item[0])
    return matches[-1][1]


def _key_argument(
    result: dict[str, Any],
    packet: AuditPacket | None,
    role: str,
) -> str:
    if packet:
        if role == "bull" and packet.key_bull_argument:
            return packet.key_bull_argument
        if role == "bear" and packet.key_bear_argument:
            return packet.key_bear_argument
        if role == "devils_advocate" and packet.devils_advocate_question:
            return packet.devils_advocate_question
    return _raw_history_argument(result, role)


def _key_argument_summary(
    result: dict[str, Any],
    packet: AuditPacket | None,
    role: str,
    limit: int = 220,
) -> str:
    packet_text = ""
    if packet:
        if role == "bull":
            packet_text = packet.key_bull_argument
        elif role == "bear":
            packet_text = packet.key_bear_argument
        elif role == "devils_advocate":
            packet_text = packet.devils_advocate_question
    source_text = packet_text or _latest_history_argument(result, role)
    return _summarize_argument_text(source_text, limit)


def _summary(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    if packet and packet.one_line_summary:
        return packet.one_line_summary
    verdict = _verdict(result)
    return str(
        verdict.get("summary")
        or verdict.get("weighted_reasoning")
        or result.get("summary")
        or "Data tidak tersedia"
    )


def _sources(result: dict[str, Any], packet: AuditPacket | None = None) -> list[str]:
    sources: list[str] = []
    if packet:
        for item in packet.evidence_used:
            if item.source and item.source not in sources:
                sources.append(item.source)
    metadata = _dict_or_empty(result.get("metadata"))
    for key in ("data_sources", "sources", "source"):
        raw = metadata.get(key) or result.get(key)
        if isinstance(raw, list):
            sources.extend(str(item) for item in raw if str(item))
        elif raw:
            sources.append(str(raw))
    unique: list[str] = []
    for source in sources:
        if source not in unique:
            unique.append(source)
    return unique


def _missing_fields(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[str]:
    if packet and packet.missing_fields:
        return list(packet.missing_fields)
    metadata = _dict_or_empty(result.get("metadata"))
    raw = result.get("missing_fields") or metadata.get("missing_fields")
    return [str(item) for item in _list_or_empty(raw) if str(item)]


def _vote_value(vote: Any, key: str) -> Any:
    if isinstance(vote, dict):
        return vote.get(key)
    return getattr(vote, key, None)


def _agent_votes(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[Any]:
    votes = _list_or_empty(result.get("agent_votes"))
    if not votes and packet:
        votes = list(packet.agent_votes)
    return votes


def _votes_by_agent(votes: list[Any]) -> dict[str, Any]:
    by_agent: dict[str, Any] = {}
    for vote in votes:
        key = _canonical(_vote_value(vote, "agent"))
        if key:
            by_agent[key] = vote
    return by_agent


def _winner_agent(result: dict[str, Any], packet: AuditPacket | None = None) -> str:
    verdict = _verdict(result)
    method = result.get("consensus_method") or verdict.get("consensus_method")
    packet_winner = packet.winner_agent if packet else None
    if _is_soft_hold_winner(method, packet_winner):
        return _soft_hold_label()
    if packet and packet.winner_agent and not _winner_missing(packet.winner_agent):
        return packet.winner_agent
    winner = (
        result.get("winner_agent")
        or result.get("confidence_winner")
        or verdict.get("winner_agent")
        or verdict.get("consensus_winner")
    )
    if _is_soft_hold_winner(method, winner):
        return _soft_hold_label()
    if not _winner_missing(winner):
        return str(winner)
    if _canonical(method) == "voting":
        voting_winners = _voting_winner_agents(result, packet)
        if voting_winners:
            return f"{', '.join(voting_winners)} (voting)"
        return "Voting majority"
    return _soft_hold_label() if _is_soft_hold_winner(method) else "Data tidak tersedia"


def _voting_winner_agents(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[str]:
    rating = _canonical_position(_rating(result, packet))
    winners: list[str] = []
    for vote in _agent_votes(result, packet):
        agent = _vote_value(vote, "agent")
        if _is_devils_advocate_agent(agent):
            continue
        if _canonical_position(_vote_value(vote, "position")) != rating:
            continue
        label = _agent_label(agent)
        if label not in winners:
            winners.append(label)
    return winners


def _support_mark(
    *,
    vote: Any,
    rating: str,
    agent_key: str,
    winner: str,
) -> str:
    explicit = _vote_value(vote, "supporting_winner")
    if explicit is True:
        return "WINNER" if _canonical(winner) == agent_key else "SUPPORTS"
    if _canonical(winner) == agent_key:
        return "WINNER"
    position = _canonical_position(_vote_value(vote, "position"))
    return "SUPPORTS" if position == _canonical_position(rating) else "DIFFERS"


def _soft_hold_override_note(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> str | None:
    verdict = _verdict(result)
    method = (
        result.get("consensus_method")
        or verdict.get("consensus_method")
        or (packet.consensus_method if packet else None)
    )
    winner = (
        result.get("winner_agent")
        or result.get("confidence_winner")
        or verdict.get("winner_agent")
        or verdict.get("consensus_winner")
        or (packet.winner_agent if packet else None)
    )
    if _is_soft_hold_winner(method, winner):
        return "Semua agen di-override oleh soft_hold_rule karena tidak ada konsensus."
    return None


def _agent_rows(
    result: dict[str, Any],
    packet: AuditPacket | None = None,
) -> list[tuple[str, str, str, str, str | None]]:
    votes = _agent_votes(result, packet)
    if not votes:
        return []
    by_agent = _votes_by_agent(votes)
    rating = _rating(result, packet)
    winner = _winner_agent(result, packet)
    rows: list[tuple[str, str, str, str, str | None]] = []
    for agent_key in _AGENT_ORDER:
        vote = by_agent.get(agent_key)
        is_adversarial = _is_devils_advocate_agent(agent_key)
        agent_label = _AGENT_LABELS[agent_key]
        if is_adversarial:
            agent_label = f"{agent_label} (adversarial)"
            if vote is None:
                vote = next(
                    (
                        candidate
                        for key, candidate in by_agent.items()
                        if _is_devils_advocate_agent(key)
                    ),
                    None,
                )
        if vote is None:
            result_text = "N/A" if is_adversarial else "NO VOTE"
            style = "dim" if is_adversarial else None
            rows.append((agent_label, "--", "--", result_text, style))
            continue
        position = str(_vote_value(vote, "position") or "--").upper()
        confidence = _confidence(_vote_value(vote, "confidence"))
        confidence_text = "--" if confidence is None else f"{confidence:.0%}"
        if is_adversarial:
            rows.append((agent_label, position, confidence_text, "N/A", "dim"))
            continue
        rows.append(
            (
                agent_label,
                position,
                confidence_text,
                _support_mark(
                    vote=vote,
                    rating=rating,
                    agent_key=agent_key,
                    winner=winner,
                ),
                None,
            )
        )
    return rows


class RichFormatter:
    """Render readable debate summaries to the terminal with Rich."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()

    def _rating_style(self, rating: str) -> str:
        normalized = str(rating or "").upper()
        if normalized == "BUY":
            return "bold green"
        if normalized == "HOLD":
            return "bold yellow"
        if normalized == "AVOID":
            return "bold red"
        return "bold white"

    def _rating_emoji(self, rating: str) -> str:
        normalized = str(rating or "").upper()
        if normalized in {"BUY", "HOLD", "AVOID", "STRONG_BUY", "SELL"}:
            return normalized
        return "UNKNOWN"

    def _confidence_bar(self, confidence: float, width: int = 20) -> str:
        try:
            value = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            value = 0.0
        filled = round(value * width)
        bar = "█" * filled + "░" * (width - filled)
        return f"{bar} {value:.0%}"

    def _terminal_confidence_bar(self, confidence: float, width: int = 20) -> str:
        if self._console_supports_unicode():
            return self._confidence_bar(confidence, width)
        try:
            value = max(0.0, min(float(confidence), 1.0))
        except (TypeError, ValueError):
            value = 0.0
        filled = round(value * width)
        bar = "#" * filled + "-" * (width - filled)
        return f"{bar} {value:.0%}"

    def _risk_governor_line(self, risk: dict) -> str:
        status = _dict_or_empty(risk).get("status", "unknown")
        labels = {
            "deployable": "Siap dieksekusi",
            "conditional_deployable": "Conditional watchlist",
            "wait_for_pullback": "Tunggu pullback",
            "watchlist_only": "Pantau saja",
            "reject": "Ditolak sistem",
        }
        return labels.get(str(status), str(status))

    def _terminal_emoji(self, rating: str) -> str:
        normalized = str(rating or "").upper()
        return normalized if normalized else "UNKNOWN"

    def _terminal_risk_governor_line(self, risk: dict) -> str:
        if self._console_supports_unicode():
            return self._risk_governor_line(risk)
        status = _dict_or_empty(risk).get("status", "unknown")
        labels = {
            "deployable": "Siap dieksekusi",
            "conditional_deployable": "Conditional watchlist",
            "wait_for_pullback": "Tunggu pullback",
            "watchlist_only": "Pantau saja",
            "reject": "Ditolak sistem",
        }
        return labels.get(str(status), str(status))

    def _console_supports_unicode(self) -> bool:
        encoding = str(getattr(self.console.file, "encoding", "") or "")
        return "utf" in encoding.lower()

    def _warning_marker(self) -> str:
        return "- "

    def _sparkle_marker(self) -> str:
        return "- "

    def _support_symbol(self, value: str) -> str:
        if value == "N/A":
            return "N/A"
        return value

    def _argument_style(self, role: str) -> str:
        if role == "bull":
            return "green"
        if role == "bear":
            return "red"
        return "yellow"

    def _build_argument_group(
        self,
        result: dict[str, Any],
        packet: AuditPacket | None,
    ) -> Table:
        table = Table.grid(padding=(0, 1))
        table.add_column(style="bold", no_wrap=True)
        table.add_column()
        for label, role in (
            ("Bull", "bull"),
            ("Bear", "bear"),
            ("Devil's Advocate", "devils_advocate"),
        ):
            table.add_row(
                Text(label, style=self._argument_style(role)),
                Text(_key_argument_summary(result, packet, role)),
            )
        return table

    def render_ticker_panel(
        self,
        result: dict,
        packet: AuditPacket | None = None,
    ) -> None:
        """Print a comprehensive one-page Rich panel for a single ticker."""
        try:
            data = result if isinstance(result, dict) else {}
            ticker = _ticker(data, packet)
            verdict = _verdict(data)
            rating = _rating(data, packet)
            rating_style = self._rating_style(rating)
            confidence = _confidence(
                verdict.get("confidence")
                or (packet.verdict_confidence if packet else None)
            )
            confidence_text = (
                "Data tidak tersedia"
                if confidence is None
                else self._terminal_confidence_bar(confidence)
            )
            current_price = verdict.get("current_price")
            fair_value = verdict.get("fair_value")
            value_gap = _price_diff_pct(fair_value, current_price)
            value_style = "green" if (value_gap or 0) >= 0 else "red"
            entry_low, entry_high = _entry_bounds(verdict)
            target = verdict.get("target_price")
            stop = verdict.get("stop_loss")
            upside = _move_pct(target, current_price)
            downside = _downside_pct(stop, current_price)
            risk = _risk(data)
            news_sentiment, news_adj = _get_news(data)

            table = Table.grid(padding=(0, 2))
            table.add_column(style="bold", no_wrap=True)
            table.add_column()
            table.add_row(
                "Rekomendasi",
                Text(
                    rating,
                    style=rating_style,
                ),
            )
            table.add_row("Keyakinan", confidence_text)
            table.add_row("Harga Kini", _money(current_price))
            table.add_row("", Rule("VALUASI", style="dim"))
            table.add_row("Nilai Wajar", _money(fair_value))
            table.add_row(
                "Selisih",
                Text(
                    f"{_signed_pct(value_gap)} dari harga kini",
                    style=value_style,
                ),
            )
            table.add_row("Status", _valuation_status(fair_value, current_price))
            table.add_row("", Rule("RENCANA TRADE", style="dim"))
            table.add_row(
                "Entry Zone",
                f"{_money(entry_low)} – {_money(entry_high)}",
            )
            table.add_row(
                "Target",
                f"{_money(target)}  ({_signed_pct(upside)})",
            )
            table.add_row(
                "Stop Loss",
                f"{_money(stop)}  ({_signed_pct(-downside if downside is not None else None)})",
            )
            table.add_row("Risk/Reward", _ratio(verdict.get("risk_reward_ratio")))
            table.add_row("Timeframe", str(verdict.get("timeframe") or "N/A"))
            table.add_row("", Rule("DEBAT", style="dim"))
            table.add_row("Ronde", str(data.get("debate_rounds") or "N/A"))
            consensus = data.get("consensus_reached")
            if consensus is None:
                consensus = verdict.get("consensus_reached")
            method = data.get("consensus_method") or verdict.get("consensus_method")
            table.add_row(
                "Konsensus",
                f"{_yes_no(consensus)} — {_method_indonesian(method)}",
            )
            table.add_row("Pemenang", _winner_agent(data, packet))
            vote_table = self._build_vote_table(data, packet)
            table.add_row("Voting Agent", vote_table)
            table.add_row("", Rule("ARGUMEN KUNCI", style="dim"))
            table.add_row("Highlight", self._build_argument_group(data, packet))

            risks = _key_risks(data)[:3]
            if risks:
                table.add_row("", Rule("RISIKO & KATALIS", style="dim"))
                for index, risk_text in enumerate(risks, start=1):
                    table.add_row(
                        "Risiko" if index == 1 else "",
                        f"{self._warning_marker()}{risk_text}",
                    )
            catalysts = _catalysts(data)[:2] if rating == "BUY" else []
            for index, catalyst in enumerate(catalysts, start=1):
                table.add_row(
                    "Katalis" if index == 1 else "",
                    f"{self._sparkle_marker()}{catalyst}",
                )

            table.add_row("", Rule("SISTEM", style="dim"))
            table.add_row("Risk Governor", self._terminal_risk_governor_line(risk))
            table.add_row("News", f"{news_sentiment} ({news_adj:+.2f})")
            table.add_row(
                "Data Sentimen",
                "tersedia"
                if news_sentiment.lower() not in {"tidak tersedia", "unknown"}
                else "tidak tersedia",
            )
            table.add_row("Dibuat", _generated_at(data, packet))

            border = {
                "BUY": "green",
                "HOLD": "yellow",
                "AVOID": "red",
            }.get(rating, "white")
            self.console.print(
                Panel(
                    table,
                    title=f"ANALISIS: {ticker}",
                    border_style=border,
                    padding=(1, 2),
                )
            )
        except Exception as exc:
            self.console.print(
                Panel(
                    f"Data tidak tersedia ({exc})",
                    title="ANALISIS: UNKNOWN",
                    border_style="white",
                )
            )

    def render_batch_summary(
        self,
        results: list[dict],
        *,
        succeeded: int | None = None,
        failed: int | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Print a readable batch summary panel."""
        try:
            rows = results if isinstance(results, list) else []
            ok_results = [row for row in rows if not row.get("error")]
            fail_results = [row for row in rows if row.get("error")]
            succeeded = len(ok_results) if succeeded is None else succeeded
            failed = len(fail_results) if failed is None else failed

            table = Table(show_header=True, header_style="bold", expand=True)
            table.add_column("Ticker", style="bold", no_wrap=True)
            table.add_column("Rating", no_wrap=True)
            table.add_column("Conf", justify="right", no_wrap=True)
            table.add_column("R/R", justify="right", no_wrap=True)
            table.add_column("Entry Zone")
            table.add_column("Target")
            table.add_column("Risk Gov")

            for row in rows:
                verdict = _verdict(row)
                rating = "ERROR" if row.get("error") else _rating(row)
                low, high = _entry_bounds(verdict)
                risk = _risk(row)
                table.add_row(
                    _ticker(row),
                    Text(rating, style=self._rating_style(rating)),
                    _pct(verdict.get("confidence")),
                    _ratio(verdict.get("risk_reward_ratio")),
                    f"{_money(low, include_prefix=False)}-{_money(high, include_prefix=False)}",
                    _money(verdict.get("target_price")),
                    self._terminal_risk_governor_line(risk),
                )

            duration_text = ""
            if duration_seconds is not None:
                minutes, seconds = divmod(max(0, int(duration_seconds)), 60)
                duration_text = f"  |  Durasi: {minutes}m {seconds:02d}s"
            footer = Text(
                f"Berhasil: {succeeded}  |  Gagal: {failed}{duration_text}",
                style="dim",
            )

            self.console.print(
                Panel(
                    Group(table, footer),
                    title="HASIL DEBATE",
                    border_style="cyan",
                    padding=(1, 2),
                )
            )
        except Exception as exc:
            self.console.print(
                Panel(
                    f"Ringkasan batch tidak tersedia ({exc})",
                    title="HASIL DEBATE",
                    border_style="white",
                )
            )

    def _build_vote_table(
        self,
        result: dict[str, Any],
        packet: AuditPacket | None,
    ) -> Table | Text | Group:
        rows = _agent_rows(result, packet)
        if not rows:
            return Text("Tidak ada data voting", style="dim")
        table = Table(show_header=True, header_style="bold", expand=False)
        table.add_column("Agent")
        table.add_column("Posisi")
        table.add_column("Keyakinan", justify="right")
        table.add_column("Hasil")
        for agent, position, confidence, result_text, style in rows:
            table.add_row(
                agent,
                position,
                confidence,
                result_text,
                style=style,
            )
        override_note = _soft_hold_override_note(result, packet)
        if override_note:
            return Group(table, Text(override_note, style="dim"))
        return table

    def _batch_recommendation_line(self, result: dict[str, Any], rating: str) -> Group:
        verdict = _verdict(result)
        ticker = _ticker(result)
        confidence = _pct(verdict.get("confidence"))
        rr = _ratio(verdict.get("risk_reward_ratio"))
        low, high = _entry_bounds(verdict)
        target = _money(verdict.get("target_price"))
        reason = _short_text(
            verdict.get("summary") or verdict.get("weighted_reasoning"),
            60,
        )
        first = f"{rating:<5} - {ticker}"
        if rating == "BUY":
            second = (
                f"   conf={confidence}  R/R={rr}\n"
                f"   Entry: {_money(low)}-{_money(high)}\n"
                f"   Target: {target}"
            )
        else:
            second = f"   conf={confidence}  {reason}"
        return Group(Text(first, style=self._rating_style(rating)), Text(second))

    def _deployable_line(self, result: dict[str, Any]) -> str:
        verdict = _verdict(result)
        low, high = _entry_bounds(verdict)
        return (
            f"{_ticker(result)} - harga di zona entry\n"
            f"   Entry: {_money(low)}-{_money(high)}  "
            f"Target: {_money(verdict.get('target_price'))}  "
            f"Stop: {_money(verdict.get('stop_loss'))}"
        )


class MarkdownFormatter:
    """Generate Bahasa Indonesia Markdown reports for debate results."""

    def generate_ticker_report(
        self,
        result: dict,
        packet: AuditPacket | None = None,
    ) -> str:
        """Generate a complete ticker report without raising."""
        try:
            data = result if isinstance(result, dict) else {}
            verdict = _verdict(data)
            ticker = _ticker(data, packet)
            rating = _rating(data, packet)
            confidence = _confidence(
                verdict.get("confidence")
                or (packet.verdict_confidence if packet else None)
            )
            confidence_text = "N/A" if confidence is None else f"{confidence:.0%}"
            current_price = verdict.get("current_price")
            fair_value = verdict.get("fair_value")
            value_gap = _price_diff_pct(fair_value, current_price)
            value_status = _valuation_status(fair_value, current_price)
            low, high = _entry_bounds(verdict)
            target = verdict.get("target_price")
            stop = verdict.get("stop_loss")
            upside = _move_pct(target, current_price)
            downside = _downside_pct(stop, current_price)
            method = data.get("consensus_method") or verdict.get("consensus_method")
            risk = _risk(data)
            news_sentiment, news_adj = _get_news(data)
            sources = _sources(data, packet)
            missing = _missing_fields(data, packet)

            lines = [
                "---",
                f"# Laporan Analisis: {ticker}",
                f"**Tanggal**: {_date_wib()}",
                f"**Run ID**: {_run_id(data, packet)}",
                "**Mode**: Multi-Agent AI Debate",
                "",
                "---",
                "",
                "## Ringkasan Eksekutif",
                "",
                "| Item | Detail |",
                "|------|--------|",
                f"| **Rekomendasi** | **{rating}** |",
                f"| **Keyakinan** | {confidence_text} |",
                f"| **Harga Saat Ini** | {_money(current_price)} |",
                f"| **Nilai Wajar** | {_money(fair_value)} |",
                f"| **Selisih** | {_signed_pct(value_gap)} ({value_status}) |",
                "",
                f"> {_summary(data, packet)}",
                "",
                "---",
                "",
                "## Rencana Trade",
                "",
                "| Parameter | Nilai |",
                "|-----------|-------|",
                f"| **Zona Entry** | {_money(low)} - {_money(high)} |",
                f"| **Target Harga** | {_money(target)} ({_signed_pct(upside)}) |",
                f"| **Stop Loss** | {_money(stop)} ({_signed_pct(-downside if downside is not None else None)}) |",
                f"| **Risk/Reward** | {_ratio(verdict.get('risk_reward_ratio'))} |",
                f"| **Timeframe** | {verdict.get('timeframe') or 'N/A'} |",
                "",
                "---",
                "",
                "## Proses Debat Multi-Agent",
                "",
                f"**Jumlah Ronde**: {data.get('debate_rounds') or 'N/A'}",
                f"**Konsensus**: {_yes_no(data.get('consensus_reached') or verdict.get('consensus_reached'))}",
                f"**Metode**: {_method_indonesian(method)}",
                "",
                "### Voting Agent",
                "",
                *self._markdown_vote_table(data, packet),
                "",
                "### Argumen Kunci",
                "",
                "**Bull (Optimis):**",
                f"> {_key_argument(data, packet, 'bull')}",
                "",
                "**Bear (Pesimis):**",
                f"> {_key_argument(data, packet, 'bear')}",
                "",
                "**Advocatus Diaboli:**",
                f"> {_key_argument(data, packet, 'devils_advocate')}",
                "",
                "---",
                "",
                "## Analisis Risiko",
                "",
            ]
            risks = _key_risks(data)
            if risks:
                lines.extend(f"- {item}" for item in risks)
            else:
                lines.append("Data risiko tidak tersedia.")
            lines.append("")

            if rating == "BUY":
                lines.extend(["## Katalis Potensial", ""])
                catalysts = _catalysts(data)
                if catalysts:
                    lines.extend(f"- {item}" for item in catalysts)
                else:
                    lines.append("Data katalis tidak tersedia.")
                lines.append("")

            lines.extend(
                [
                    "---",
                    "",
                    "## Evaluasi Sistem",
                    "",
                    "| Komponen | Hasil |",
                    "|----------|-------|",
                    f"| **Risk Governor** | {RichFormatter()._risk_governor_line(risk)} |",
                    f"| **Sentimen Berita** | {news_sentiment} ({news_adj:+.2f}) |",
                    f"| **Data Tersedia** | {', '.join(sources) if sources else 'Tidak ada'} |",
                    f"| **Field Hilang** | {', '.join(missing) if missing else 'Tidak ada'} |",
                    "",
                    "---",
                    "",
                    "## Metodologi",
                    "",
                    "Analisis ini dihasilkan oleh sistem",
                    "**Multi-Agent AI Debate** menggunakan",
                    "Google Gemini sebagai engine LLM.",
                    "Lima agent AI dengan perspektif berbeda",
                    "(Bull, Bear, Chartist, Fundamental Scout,",
                    "Sentiment Specialist) berdebat selama",
                    f"{data.get('debate_rounds') or 'N/A'} ronde sebelum CIO Agent",
                    "mengambil keputusan final.",
                    "",
                    "Setiap keputusan dapat diaudit",
                    "berdasarkan bukti yang tersedia",
                    "pada waktu analisis.",
                    "",
                    "---",
                    "*Laporan ini dihasilkan otomatis oleh",
                    "IDX Fundamental Analysis System.",
                    "Bukan merupakan saran investasi resmi.*",
                ]
            )
            return "\n".join(lines)
        except Exception as exc:
            ticker = _ticker(result if isinstance(result, dict) else {}, packet)
            return "\n".join(
                [
                    "---",
                    f"# Laporan Analisis: {ticker}",
                    f"**Tanggal**: {_date_wib()}",
                    "**Mode**: Multi-Agent AI Debate",
                    "",
                    "Data tidak tersedia.",
                    f"Error formatter: {exc}",
                ]
            )

    def generate_batch_summary(self, results: list[dict], run_id: str) -> str:
        """Generate a Markdown batch summary without raising."""
        try:
            rows = results if isinstance(results, list) else []
            grouped = {"BUY": [], "HOLD": [], "AVOID": []}
            for row in rows:
                grouped.setdefault(_rating(row), []).append(row)
            deployable = [
                row
                for row in grouped.get("BUY", [])
                if _risk(row).get("status") == "deployable"
            ]
            waiting = [
                row
                for row in grouped.get("BUY", [])
                if _risk(row).get("status") == "wait_for_pullback"
            ]
            lines = [
                "---",
                "# Ringkasan Analisis Batch",
                f"**Tanggal**: {_date_wib()}",
                f"**Run ID**: {run_id}",
                f"**Total Saham**: {len(rows)}",
                "",
                "## Hasil Keseluruhan",
                "",
                "| Rating | Jumlah | Saham |",
                "|--------|--------|-------|",
                self._rating_summary_row("BUY", grouped.get("BUY", [])),
                self._rating_summary_row("HOLD", grouped.get("HOLD", [])),
                self._rating_summary_row("AVOID", grouped.get("AVOID", [])),
                "",
                "## Saham yang Dapat Dieksekusi",
                "",
            ]
            if deployable:
                for row in deployable:
                    lines.extend(self._deployable_summary(row))
            else:
                lines.append("Tidak ada.")
                lines.append("")

            lines.extend(["## Saham dalam Watchlist", ""])
            if waiting:
                for row in waiting:
                    verdict = _verdict(row)
                    low, high = _entry_bounds(verdict)
                    lines.extend(
                        [
                            f"### {_ticker(row)}",
                            f"- Tunggu pullback ke: {_money(low)} - {_money(high)}",
                            "",
                        ]
                    )
            else:
                lines.append("Tidak ada.")
                lines.append("")

            lines.extend(["---", "*Generated by IDX Fundamental Analysis*"])
            return "\n".join(lines)
        except Exception as exc:
            return "\n".join(
                [
                    "---",
                    "# Ringkasan Analisis Batch",
                    f"**Tanggal**: {_date_wib()}",
                    f"**Run ID**: {run_id}",
                    "",
                    "Data tidak tersedia.",
                    f"Error formatter: {exc}",
                ]
            )

    def _markdown_vote_table(
        self,
        result: dict[str, Any],
        packet: AuditPacket | None,
    ) -> list[str]:
        rows = _agent_rows(result, packet)
        if not rows:
            return ["Tidak ada data voting"]
        lines = [
            "| Agent | Posisi | Keyakinan |",
            "|-------|--------|-----------|",
        ]
        for agent, position, confidence, _outcome, _style in rows:
            lines.append(f"| {agent} | {position} | {confidence} |")
        override_note = _soft_hold_override_note(result, packet)
        if override_note:
            lines.extend(["", f"*{override_note}*"])
        return lines

    def _rating_summary_row(self, label: str, rows: list[dict[str, Any]]) -> str:
        tickers = ", ".join(_ticker(row) for row in rows) if rows else "-"
        return f"| {label} | {len(rows)} | {tickers} |"

    def _deployable_summary(self, result: dict[str, Any]) -> list[str]:
        verdict = _verdict(result)
        low, high = _entry_bounds(verdict)
        target = verdict.get("target_price")
        current = verdict.get("current_price")
        return [
            f"### {_ticker(result)}",
            f"- Entry: {_money(low)} - {_money(high)}",
            f"- Target: {_money(target)} ({_signed_pct(_move_pct(target, current))})",
            f"- Stop: {_money(verdict.get('stop_loss'))}",
            (
                f"- R/R: {_ratio(verdict.get('risk_reward_ratio'))} | "
                f"Keyakinan: {_pct(verdict.get('confidence'))}"
            ),
            "",
        ]


DEFAULT_RICH = RichFormatter()
DEFAULT_MD = MarkdownFormatter()
