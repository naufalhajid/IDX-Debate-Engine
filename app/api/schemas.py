from pydantic import BaseModel, Field, field_validator


class DebateStreamRequest(BaseModel):
    tickers: list[str] = Field(..., min_length=1, max_length=10)

    @field_validator("tickers")
    @classmethod
    def tickers_uppercase(cls, value: list[str]) -> list[str]:
        cleaned = [ticker.strip().upper() for ticker in value if ticker.strip()]
        if not cleaned:
            raise ValueError("Pilih minimal satu ticker untuk menjalankan debate.")
        return cleaned
