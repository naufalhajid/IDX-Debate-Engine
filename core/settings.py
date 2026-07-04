from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from core.idx_market_params import INDONESIA_RISK_FREE, INDONESIA_TOTAL_ERP

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

    # Output artifact paths
    output_dir: Path = Path("output")
    results_path: Path = Path("output/full_batch_results.json")
    merged_results_path: Path = Path("output/merged_batch_results.json")
    sector_cache_path: Path = Path("output/sector_cache.json")
    adaptive_planner_path: Path = Path("output/planner/plan_log.jsonl")
    execution_ledger_path: Path = Path("output/ledger/execution_ledger.jsonl")
    ops_telemetry_path: Path = Path("output/telemetry/telemetry_log.jsonl")
    audit_log_path: Path = Path("output/audit/audit_log.jsonl")
    rag_evidence_log_path: Path = Path("output/rag_evidence/evidence_log.jsonl")
    backtest_memory_path: Path = Path("output/backtest/backtest_memory.jsonl")
    observations_path: Path = Path("output/observations/observations.jsonl")
    debates_dir: Path = Path("output/debates")

    GOOGLE_SERVICE_ACCOUNT: str = "{}"
    GOOGLE_DRIVE_EMAILS: str = '["example@gmail.com"]'

    # ── LLM Provider Configuration ────────────────────────────────────────────
    # DEFAULT_LLM_PROVIDER: which backend to use by default.
    # Options: "gemini" | "anthropic" | "codex"
    DEFAULT_LLM_PROVIDER: str = "gemini"

    # Gemini AI
    GEMINI_API_KEY: str = ""
    GEMINI_FLASH_MODEL: str = "gemini-2.5-flash"
    GEMINI_PRO_MODEL: str = "gemini-3.1-pro-preview"

    # Anthropic OAuth
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_OAUTH_CLIENT_ID: str = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
    ANTHROPIC_FLASH_MODEL: str = "claude-3-5-haiku-latest"
    ANTHROPIC_PRO_MODEL: str = "claude-3-5-sonnet-latest"

    # OpenAI Codex OAuth
    CODEX_OAUTH_CLIENT_ID: str = "app_EMoamEEZ73f0CkXaXp7hrann"
    CODEX_FLASH_MODEL: str = "gpt-5.4-mini"
    CODEX_PRO_MODEL: str = "gpt-5.5"
    CODEX_FLASH_REASONING_EFFORT: str = "medium"
    CODEX_PRO_REASONING_EFFORT: str = "xhigh"
    CODEX_FLASH_REQUEST_TIMEOUT_SECONDS: int = 120
    CODEX_PRO_REQUEST_TIMEOUT_SECONDS: int = 180

    # Token storage directory for OAuth credentials
    TOKEN_STORAGE_DIR: str = "output/tokens"

    # Debate runtime guard. Codex high/xhigh runs need more wall-clock time.
    DEBATE_TIMEOUT_SECONDS: int = 300
    CODEX_DEBATE_TIMEOUT_SECONDS: int = 900

    # ── Conviction Scoring Weights (must sum to 1.0) ─────────────────────────
    # Override via: CONVICTION_WEIGHT_CONFIDENCE=0.6 CONVICTION_WEIGHT_RR_RATIO=0.4
    CONVICTION_WEIGHT_CONFIDENCE: float = 0.50
    CONVICTION_WEIGHT_RR_RATIO: float = 0.50
    CONVICTION_RR_NORMALIZATION_CAP: float = 5.0
    FORECAST_EV_RANKING_ENABLED: bool = False
    PIPELINE_AUTO_EVALUATE_MEMORY: bool = False

    # Stockbit API
    STOCKBIT_MAX_WORKERS: int = 10

    # ── IndoBERT sentiment prior (Gap P7 Tier 2 / task D1) ───────────────────
    # Opt-in: first use downloads the model (~500 MB) from HuggingFace and
    # requires `uv sync --extra sentiment` (transformers + torch). When the
    # model cannot load, the sentiment scout degrades to LLM-only behavior.
    SENTIMENT_INDOBERT_ENABLED: bool = False
    SENTIMENT_INDOBERT_MODEL: str = "mdhugol/indonesia-bert-sentiment-classification"

    # ── Market Holidays ──────────────────────────────────────────────────────────
    # IDX_ADDITIONAL_HOLIDAYS: Comma-separated list of YYYY-MM-DD dates to exclude from RAG staleness
    IDX_ADDITIONAL_HOLIDAYS: str = ""

    # ── Orchestrator Configuration ────────────────────────────────────────────────
    # CANDIDATES_MAX_AGE_HOURS: max umur top10_candidates.json sebelum dianggap stale
    # CANDIDATES_AUTO_RERUN: jika True, jalankan run_quant_filter.py otomatis
    CANDIDATES_MAX_AGE_HOURS: float = 48.0
    CANDIDATES_AUTO_RERUN: bool = True

    # ── Market Regime (IHSG ^JKSE realized volatility proxy) ─────────────────
    REGIME_VOLATILITY_HIGH_THRESHOLD: float = 0.02  # daily std >= 2% → HIGH
    REGIME_VOLATILITY_LOW_THRESHOLD: float = 0.01  # daily std < 1%  → LOW
    REGIME_VOLATILITY_LOOKBACK_DAYS: int = 20
    REGIME_DEFENSIVE_WEEKLY_DROP_THRESHOLD: float = 0.05

    # Regime override params (di-merge ke ORCHESTRATOR_CONFIG via get_regime_params)
    # HIGH = lebih konservatif, LOW = lebih agresif. NORMAL tidak punya override.
    REGIME_DEFENSIVE_TOP_N: int = 3
    REGIME_DEFENSIVE_RPM_LIMIT: int = 5
    REGIME_DEFENSIVE_MIN_CONVICTION: float = 0.70
    REGIME_DEFENSIVE_MAX_RR_FOR_SCORING: float = 4.0
    REGIME_HIGH_TOP_N: int = 2  # kurangi exposure di pasar volatile
    REGIME_HIGH_RPM_LIMIT: int = 5  # hemat budget API
    REGIME_HIGH_RR_CAP: float = 4.0  # tighten cap (R/R > 4x lebih mencurigakan)
    REGIME_HIGH_MIN_CONVICTION: float = 0.45  # standar lebih ketat
    REGIME_LOW_TOP_N: int = 5  # opportunity lebih banyak di pasar tenang
    REGIME_LOW_RPM_LIMIT: int = 15
    REGIME_LOW_RR_CAP: float = 6.0  # lebih toleran ke R/R tinggi
    REGIME_LOW_MIN_CONVICTION: float = 0.20
    # RECOVERY: vol HIGH tapi 5d return >= threshold → bounce setelah koreksi
    # top_n lebih besar dari HIGH (2) karena momentum berbalik positif
    REGIME_HIGH_RECOVERY_WEEKLY_THRESHOLD: float = 0.10
    REGIME_RECOVERY_TOP_N: int = 4
    REGIME_RECOVERY_RPM_LIMIT: int = 8
    REGIME_RECOVERY_RR_CAP: float = 4.0
    REGIME_RECOVERY_MIN_CONVICTION: float = 0.40

    # ── Portfolio Diversification ────────────────────────────────────────────
    # PORTFOLIO_MAX_PER_SECTOR: max saham per sektor dalam top N
    # PORTFOLIO_MIN_CONVICTION: minimum conviction score agar eligible masuk top N
    PORTFOLIO_MAX_PER_SECTOR: int = 2
    PORTFOLIO_MIN_CONVICTION: float = 0.30

    # ── Trade Envelope ────────────────────────────────────────────────────────
    # Hard floor: stop tidak boleh lebih dari X% dari current price
    TRADE_ENVELOPE_MAX_STOP_LOSS_PCT: float = 0.10
    # Noise gate thresholds (ATR multiplier):
    #   < HARD → hard reject (HOLD 0.40, no entry/target/stop)
    #   HARD–CLEAN → conditional BUY (confidence capped, stop_near_noise flagged)
    #   >= CLEAN → clean setup
    TRADE_ENVELOPE_HARD_NOISE_ATR_MULTIPLIER: float = 1.00
    TRADE_ENVELOPE_CLEAN_NOISE_ATR_MULTIPLIER: float = 1.50
    TRADE_ENVELOPE_CONDITIONAL_CONFIDENCE_CAP: float = 0.60

    # ── Fair Value / CAPM Calibration ─────────────────────────────────────────
    SBN_10Y_YIELD: float = INDONESIA_RISK_FREE  # SBN 10-year fallback; live cache can override
    IDX_ERP: float = INDONESIA_TOTAL_ERP  # Damodaran Indonesia total ERP, Jan 5 2026
    DEFAULT_BETA: float = 1.0       # beta for unknown tickers (market weight)

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

    @model_validator(mode="after")
    def validate_regime_config(self) -> "Settings":
        """Pastikan market-regime override tidak membuat pipeline unsafe."""
        if self.REGIME_VOLATILITY_LOOKBACK_DAYS < 2:
            raise ValueError("REGIME_VOLATILITY_LOOKBACK_DAYS must be >= 2.")
        if self.REGIME_VOLATILITY_LOW_THRESHOLD < 0:
            raise ValueError("REGIME_VOLATILITY_LOW_THRESHOLD must be >= 0.")
        if self.REGIME_DEFENSIVE_WEEKLY_DROP_THRESHOLD <= 0:
            raise ValueError("REGIME_DEFENSIVE_WEEKLY_DROP_THRESHOLD must be > 0.")
        if (
            self.REGIME_VOLATILITY_HIGH_THRESHOLD
            <= self.REGIME_VOLATILITY_LOW_THRESHOLD
        ):
            raise ValueError(
                "REGIME_VOLATILITY_HIGH_THRESHOLD must be greater than "
                "REGIME_VOLATILITY_LOW_THRESHOLD."
            )

        positive_int_fields = (
            "REGIME_DEFENSIVE_TOP_N",
            "REGIME_DEFENSIVE_RPM_LIMIT",
            "REGIME_HIGH_TOP_N",
            "REGIME_HIGH_RPM_LIMIT",
            "REGIME_LOW_TOP_N",
            "REGIME_LOW_RPM_LIMIT",
            "REGIME_RECOVERY_TOP_N",
            "REGIME_RECOVERY_RPM_LIMIT",
        )
        for field_name in positive_int_fields:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be > 0.")

        positive_float_fields = (
            "REGIME_DEFENSIVE_MAX_RR_FOR_SCORING",
            "REGIME_HIGH_RR_CAP",
            "REGIME_LOW_RR_CAP",
            "REGIME_RECOVERY_RR_CAP",
        )
        for field_name in positive_float_fields:
            if getattr(self, field_name) <= 0:
                raise ValueError(f"{field_name} must be > 0.")

        conviction_fields = (
            "REGIME_DEFENSIVE_MIN_CONVICTION",
            "REGIME_HIGH_MIN_CONVICTION",
            "REGIME_LOW_MIN_CONVICTION",
            "REGIME_RECOVERY_MIN_CONVICTION",
        )
        for field_name in conviction_fields:
            value = getattr(self, field_name)
            if not 0 <= value <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1.")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
