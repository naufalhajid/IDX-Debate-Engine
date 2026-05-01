from functools import lru_cache
from typing import Any, Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=f"{BASE_PATH}/.env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # .env
    ENVIRONMENT: Literal["dev", "prod"] = "dev"

    # FastAPI
    FASTAPI_APP_VERSION: str = "0.0.1"
    FASTAPI_API_V1_PATH: str = "/api/v1"
    FASTAPI_TITLE: str = "IDX-Fundamental API"
    FASTAPI_DESCRIPTION: str = "IDX-Fundamental API and endpoints"
    FASTAPI_DOCS_URL: str = "/docs"
    FASTAPI_REDOC_URL: str = "/redoc"
    FASTAPI_OPENAPI_URL: str | None = "/openapi"
    FASTAPI_STATIC_FILES: bool = True

    # .env
    DATABASE_TYPE: Literal["sqlite", "postgresql"] = "sqlite"
    DATABASE_HOST: Optional[str] = "localhost"
    DATABASE_PORT: int = 5432
    DATABASE_USER: str = "db_user"
    DATABASE_PASSWORD: str = "db_password"
    DATABASE_ECHO: bool | Literal["debug"] = False
    DATABASE_POOL_ECHO: bool | Literal["debug"] = False
    DATABASE_SETUP_DROP_TABLE: bool = False

    # CORS
    MIDDLEWARE_CORS: bool = True
    CORS_ALLOWED_ORIGINS: list[str] = [
        "http://127.0.0.1:8000",
        "http://localhost:5173",
    ]
    CORS_EXPOSE_HEADERS: list[str] = [
        "X-Request-ID",
    ]

    DATETIME_TIMEZONE: str = "Asia/Jakarta"
    DATETIME_FORMAT: str = "%Y-%m-%d %H:%M:%S"

    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</> | <level>{level: <8}</> | <cyan>{file}</>:<cyan>{line}</> <cyan>{function}</> | <level>{message}</>"
    LOG_FILE_ACCESS_LEVEL: str = "INFO"
    LOG_FILE_ERROR_LEVEL: str = "ERROR"
    LOG_ACCESS_FILENAME: str = "logs/success.log"
    LOG_ERROR_FILENAME: str = "logs/error.log"
    LOG_APP_FILENAME: str = "logs/app.log"

    GOOGLE_SERVICE_ACCOUNT: str = "{}"
    GOOGLE_DRIVE_EMAILS: str = '["example@gmail.com"]'

    # Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_FLASH_MODEL: str = "gemini-2.5-flash"
    GEMINI_PRO_MODEL: str = "gemini-2.5-pro"

    # ── Conviction Scoring Weights (must sum to 1.0) ─────────────────────────
    # Override via: CONVICTION_WEIGHT_CONFIDENCE=0.6 CONVICTION_WEIGHT_RR_RATIO=0.4
    CONVICTION_WEIGHT_CONFIDENCE: float = 0.50
    CONVICTION_WEIGHT_RR_RATIO: float = 0.50
    CONVICTION_RR_NORMALIZATION_CAP: float = 5.0

    # Stockbit API
    STOCKBIT_MAX_WORKERS: int = 10

    # ── Orchestrator Configuration ────────────────────────────────────────────────
    # CANDIDATES_MAX_AGE_HOURS: max umur top10_candidates.json sebelum dianggap stale
    # CANDIDATES_AUTO_RERUN: jika True, jalankan run_quant_filter.py otomatis
    CANDIDATES_MAX_AGE_HOURS: float = 72.0
    CANDIDATES_AUTO_RERUN: bool = True

    # ── Market Regime (IHSG ^JKSE realized volatility proxy) ─────────────────
    REGIME_VOLATILITY_HIGH_THRESHOLD: float = 0.02   # daily std >= 2% → HIGH
    REGIME_VOLATILITY_LOW_THRESHOLD: float = 0.01    # daily std < 1%  → LOW
    REGIME_VOLATILITY_LOOKBACK_DAYS: int = 20

    # ── Portfolio Diversification ────────────────────────────────────────────
    # PORTFOLIO_MAX_PER_SECTOR: max saham per sektor dalam top N
    # PORTFOLIO_MIN_CONVICTION: minimum conviction score agar eligible masuk top N
    PORTFOLIO_MAX_PER_SECTOR: int = 2
    PORTFOLIO_MIN_CONVICTION: float = 0.30

    @model_validator(mode="before")
    @classmethod
    def check_env(cls, values: Any) -> Any:
        if values.get("ENVIRONMENT") == "prod":
            values["FASTAPI_OPENAPI_URL"] = None
            values["FASTAPI_STATIC_FILES"] = False
        return values

    @model_validator(mode="after")
    def validate_conviction_weights(self) -> "Settings":
        """Pastikan conviction weights selalu sum = 1.0 untuk mencegah silent score corruption."""
        total = self.CONVICTION_WEIGHT_CONFIDENCE + self.CONVICTION_WEIGHT_RR_RATIO
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"CONVICTION_WEIGHT_CONFIDENCE ({self.CONVICTION_WEIGHT_CONFIDENCE}) + "
                f"CONVICTION_WEIGHT_RR_RATIO ({self.CONVICTION_WEIGHT_RR_RATIO}) "
                f"harus sum = 1.0, got {total:.6f}. "
                "Periksa environment variables."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
