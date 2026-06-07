"""Typed registry for stock-analysis tool wrappers."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


STOCKBIT_BASE_URL = "https://exodus.stockbit.com"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ToolSpec(BaseModel):
    """Typed contract for an agent-callable tool."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    callable: Callable[..., Any]


class ToolExecutionRecord(BaseModel):
    """Validated execution metadata for one agent-callable tool run."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str
    status: Literal["success", "failed"]
    run_id: str | None = None
    started_at: datetime = Field(default_factory=_utc_now)
    duration_seconds: float
    input_payload: dict[str, Any] | None = None
    output_payload: dict[str, Any] | None = None
    error: str | None = None


class FetchPriceInput(BaseModel):
    ticker: str


class FetchPriceOutput(BaseModel):
    price: float
    timestamp: datetime = Field(default_factory=_utc_now)
    source: str


class FetchFundamentalsInput(BaseModel):
    ticker: str


class FetchFundamentalsOutput(BaseModel):
    pe_ratio: float | None
    market_cap: float | None
    revenue: float | None


class FairValueInput(BaseModel):
    ticker: str
    fundamentals: dict[str, Any] | FetchFundamentalsOutput


class FairValueOutput(BaseModel):
    fair_value: float | None
    fair_value_base: float | None = None
    fair_value_low: float | None = None
    fair_value_high: float | None = None
    range_pct: float | None = None
    risk_overvalued: bool = False
    method: str
    confidence: str


class PositionSizeInput(BaseModel):
    ticker: str
    price: float
    portfolio_value: float


class PositionSizeOutput(BaseModel):
    shares: int
    weight: float
    rationale: str


class ToolRegistry:
    """In-memory registry of typed tool specifications."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def list_tools(self) -> list[str]:
        return list(self._tools)


def execute_tool(
    registry: ToolRegistry,
    tool_name: str,
    input_data: BaseModel | dict[str, Any],
    *,
    run_id: str | None = None,
    ledger: list[ToolExecutionRecord] | None = None,
) -> ToolExecutionRecord:
    """Execute a registered tool with deterministic I/O validation."""
    started_at = _utc_now()
    started = time.perf_counter()
    input_payload: dict[str, Any] | None = None

    def build_record(
        *,
        status: Literal["success", "failed"],
        output_payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> ToolExecutionRecord:
        record = ToolExecutionRecord(
            tool_name=tool_name,
            status=status,
            run_id=run_id,
            started_at=started_at,
            duration_seconds=time.perf_counter() - started,
            input_payload=input_payload,
            output_payload=output_payload,
            error=error,
        )
        if ledger is not None:
            ledger.append(record)
        return record

    try:
        spec = registry.get(tool_name)
    except KeyError:
        return build_record(status="failed", error=f"Unknown tool: {tool_name}")

    try:
        payload = _coerce_input(spec.input_model, input_data)
        input_payload = payload.model_dump(mode="json")
    except Exception as exc:
        return build_record(
            status="failed",
            error=f"Input validation failed for {tool_name}: {exc}",
        )

    try:
        raw_output = spec.callable(payload)
    except Exception as exc:
        return build_record(status="failed", error=f"Tool execution failed: {exc}")

    try:
        output = spec.output_model.model_validate(raw_output)
    except Exception as exc:
        return build_record(
            status="failed",
            error=f"Output validation failed for {tool_name}: {exc}",
        )

    return build_record(
        status="success",
        output_payload=output.model_dump(mode="json"),
    )


def fetch_price_tool(input_data: FetchPriceInput | dict[str, Any]) -> FetchPriceOutput:
    """Fetch the latest Stockbit orderbook price for one ticker."""
    payload = _coerce_input(FetchPriceInput, input_data)
    ticker = _normalize_ticker(payload.ticker)

    from services.stockbit_api_client import StockbitApiClient

    response = StockbitApiClient().get(
        f"{STOCKBIT_BASE_URL}/company-price-feed/v2/orderbook/companies/{ticker}"
    )
    data = response.get("data") if isinstance(response, dict) else None
    price = _first_number(data or {}, "lastprice", "last_price", "price", "close")
    if price is None:
        raise ValueError(f"Unable to resolve latest price for {ticker}.")

    return FetchPriceOutput(price=price, source="stockbit")


def fetch_fundamentals_tool(
    input_data: FetchFundamentalsInput | dict[str, Any],
) -> FetchFundamentalsOutput:
    """Fetch and normalize a minimal Stockbit fundamentals snapshot."""
    payload = _coerce_input(FetchFundamentalsInput, input_data)
    ticker = _normalize_ticker(payload.ticker)

    from services.fair_value_calculator import extract_keystats
    from services.stockbit_api_client import StockbitApiClient

    response = StockbitApiClient().get(
        f"{STOCKBIT_BASE_URL}/keystats/ratio/v1/{ticker}?year_limit=10"
    )
    if not isinstance(response, dict):
        raise ValueError(f"Unable to resolve fundamentals for {ticker}.")

    stats = extract_keystats(response, ticker=ticker)
    raw_stats = response.get("data", {}).get("stats", {})

    return FetchFundamentalsOutput(
        pe_ratio=_none_if_zero(stats.raw_pe_current),
        market_cap=_first_number(raw_stats, "market_cap"),
        revenue=_find_number(response, ("revenue", "total revenue", "sales")),
    )


def fair_value_tool(input_data: FairValueInput | dict[str, Any]) -> FairValueOutput:
    """Run the existing weighted fair-value calculator behind typed I/O."""
    payload = _coerce_input(FairValueInput, input_data)

    from services.fair_value_calculator import FairValueCalculator, KeyStats

    fundamentals = (
        payload.fundamentals.model_dump()
        if isinstance(payload.fundamentals, BaseModel)
        else payload.fundamentals
    )
    stats = KeyStats(
        ticker=_normalize_ticker(payload.ticker),
        raw_pe_current=_number_or_zero(fundamentals.get("pe_ratio")),
        shares_outstanding=_number_or_zero(fundamentals.get("shares_outstanding")),
        current_price=_number_or_zero(fundamentals.get("current_price")),
        eps_ttm=_number_or_zero(fundamentals.get("eps_ttm")),
        book_value_per_share=_number_or_zero(fundamentals.get("book_value_per_share")),
        dps=_number_or_zero(fundamentals.get("dps")),
    )
    result = FairValueCalculator(stats).fair_value_weighted()

    return FairValueOutput(
        fair_value=result.get("fair_value"),
        fair_value_base=result.get("fair_value_base"),
        fair_value_low=result.get("fair_value_low"),
        fair_value_high=result.get("fair_value_high"),
        range_pct=result.get("range_pct"),
        risk_overvalued=bool(result.get("risk_overvalued")),
        method="weighted",
        confidence=str(result.get("confidence") or "UNKNOWN"),
    )


def position_size_tool(
    input_data: PositionSizeInput | dict[str, Any],
) -> PositionSizeOutput:
    """Create a one-candidate lot-sized position using the existing sizer."""
    payload = _coerce_input(PositionSizeInput, input_data)
    ticker = _normalize_ticker(payload.ticker)

    from core.quant_filter.position_sizer import calculate_positions

    result = calculate_positions(
        [
            {
                "ticker": ticker,
                "rating": "BUY",
                "confidence": 0.50,
                "current_price": payload.price,
                "stop_loss": payload.price * 0.95,
                "rr_ratio": 2.0,
            }
        ],
        {
            "total_capital": payload.portfolio_value,
            "max_loss_pct": 0.02,
            "max_positions": 1,
        },
    )
    position = (result.get("positions") or [{}])[0]
    shares = int(position.get("shares") or 0)
    weight = float(position.get("allocation_pct") or 0.0)
    rationale = (
        f"Allocated {shares} shares of {ticker} using lot-sized position sizing."
        if shares > 0
        else f"No position sized for {ticker}; portfolio value may be too small."
    )

    return PositionSizeOutput(shares=shares, weight=weight, rationale=rationale)


def _coerce_input[T: BaseModel](
    model: type[T],
    input_data: T | dict[str, Any],
) -> T:
    if isinstance(input_data, model):
        return input_data
    return model.model_validate(input_data)


def _normalize_ticker(ticker: str) -> str:
    normalized = ticker.strip().upper()
    if not normalized:
        raise ValueError("ticker must be non-empty.")
    return normalized.removesuffix(".JK")


def _first_number(payload: dict[str, Any], *keys: str) -> float | None:
    lower_key_map = {str(key).lower(): value for key, value in payload.items()}
    for key in keys:
        value = _optional_number(lower_key_map.get(key.lower()))
        if value is not None:
            return value
    return None


def _find_number(payload: Any, key_candidates: tuple[str, ...]) -> float | None:
    if isinstance(payload, dict):
        lower_candidates = {key.lower() for key in key_candidates}
        for key, value in payload.items():
            if str(key).strip().lower() in lower_candidates:
                number = _optional_number(value)
                if number is not None:
                    return number
            found = _find_number(value, key_candidates)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _find_number(item, key_candidates)
            if found is not None:
                return found
    return None


def _optional_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _number_or_zero(value: Any) -> float:
    return _optional_number(value) or 0.0


def _none_if_zero(value: float) -> float | None:
    return value if value else None


DEFAULT_REGISTRY = ToolRegistry()
DEFAULT_REGISTRY.register(
    ToolSpec(
        name="FetchPriceTool",
        description="Fetch the latest ticker price from Stockbit.",
        input_model=FetchPriceInput,
        output_model=FetchPriceOutput,
        callable=fetch_price_tool,
    )
)
DEFAULT_REGISTRY.register(
    ToolSpec(
        name="FetchFundamentalsTool",
        description="Fetch a minimal Stockbit fundamentals snapshot for one ticker.",
        input_model=FetchFundamentalsInput,
        output_model=FetchFundamentalsOutput,
        callable=fetch_fundamentals_tool,
    )
)
DEFAULT_REGISTRY.register(
    ToolSpec(
        name="FairValueTool",
        description="Calculate weighted fair value from normalized fundamentals.",
        input_model=FairValueInput,
        output_model=FairValueOutput,
        callable=fair_value_tool,
    )
)
DEFAULT_REGISTRY.register(
    ToolSpec(
        name="PositionSizeTool",
        description="Calculate a lot-sized position for one ticker and portfolio value.",
        input_model=PositionSizeInput,
        output_model=PositionSizeOutput,
        callable=position_size_tool,
    )
)
