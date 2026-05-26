from pydantic import BaseModel, ConfigDict, Field, field_validator


class DebateStreamRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=10)
    total_capital: float = Field(default=1_000_000.0, gt=0)
    max_loss_pct: float = Field(default=0.02, gt=0, le=1)
    max_positions: int = Field(default=5, ge=1, le=20)

    @field_validator("tickers")
    @classmethod
    def tickers_uppercase(cls, value: list[str]) -> list[str]:
        cleaned = [ticker.strip().upper() for ticker in value if ticker.strip()]
        if not cleaned:
            raise ValueError("Pilih minimal satu ticker untuk menjalankan debate.")
        return cleaned


class StockSchema(BaseModel):  # QW-FIX-5
    model_config = ConfigDict(from_attributes=True)

    ticker: str
    name: str | None = None
    market_cap: float | None = None
    home_page: str | None = None
