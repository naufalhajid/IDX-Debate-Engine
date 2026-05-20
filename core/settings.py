from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_PATH = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT_PATH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_PATH / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # .env
    ENVIRONMENT: Literal["dev", "prod"] = "dev"

    # .env
    DATABASE_TYPE: str = "sqlite"
    DATABASE_PATH: str = "db/idx-fundamental.db"
    # Compatibility placeholders. Runtime database support is SQLite-only.
    DATABASE_HOST: Optional[str] = None
    DATABASE_PORT: int = 5432
    DATABASE_USER: str = ""
    DATABASE_PASSWORD: str = ""
    DATABASE_ECHO: bool | Literal["debug"] = False
    DATABASE_POOL_ECHO: bool | Literal["debug"] = False
    DATABASE_SETUP_DROP_TABLE: bool = False

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
    GEMINI_FLASH_MODEL: str = "gemini-3.1-flash-lite"
    GEMINI_PRO_MODEL: str = "gemini-3.1-pro-preview"

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
    CANDIDATES_MAX_AGE_HOURS: float = 48.0
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

    @property
    def database_path(self) -> Path:
        path = Path(self.DATABASE_PATH)
        if path.is_absolute():
            return path
        return ROOT_PATH / path

    @property
    def sqlite_sync_url(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def sqlite_async_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.database_path.as_posix()}"

    @model_validator(mode="after")
    def validate_database_config(self) -> "Settings":
        database_type = str(self.DATABASE_TYPE or "sqlite").lower().strip() or "sqlite"
        if database_type != "sqlite":
            raise ValueError(
                "Only SQLite is supported by the current DB engine. "
                "Set DATABASE_TYPE=sqlite."
            )
        self.DATABASE_TYPE = database_type
        return self

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
