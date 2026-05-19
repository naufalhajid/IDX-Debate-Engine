import logging
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from core.settings import settings
from db.models import (
    Base,
    BaseModel,
    FLOAT,
    INT_PK,
    TIMESTAMP,
    UPDATED_TIMESTAMP,
    VARCHAR,
)
from db.models.fundamental import (
    BalanceSheet,
    CashFlowStatement,
    CurrentValuation,
    Dividend,
    Fundamental,
    Growth,
    IncomeStatement,
    ManagementEffectiveness,
    MarketRank,
    PerShare,
    PricePerformance,
    Profitability,
    Solvency,
    Stat,
)
from db.models.key_analysis import KeyAnalysis
from db.models.sentiment import Sentiment
from db.models.stock import Stock
from db.models.stock_price import StockPrice
from utils.logger_config import InterceptHandler

db_path = str(settings.database_path)

__all__ = [
    "BalanceSheet",
    "CashFlowStatement",
    "CurrentValuation",
    "DB",
    "Dividend",
    "Base",
    "BaseModel",
    "FLOAT",
    "Fundamental",
    "Growth",
    "INT_PK",
    "IncomeStatement",
    "KeyAnalysis",
    "ManagementEffectiveness",
    "MarketRank",
    "PerShare",
    "PricePerformance",
    "Profitability",
    "Sentiment",
    "Solvency",
    "Stat",
    "Stock",
    "StockPrice",
    "TIMESTAMP",
    "UPDATED_TIMESTAMP",
    "VARCHAR",
    "database",
    "db_path",
]

logging.basicConfig(handlers=[InterceptHandler()], level=0)
logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)


class DB:
    def __init__(self):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(
            settings.sqlite_sync_url, echo=settings.DATABASE_ECHO
        )
        self._engine_async = create_async_engine(
            settings.sqlite_async_url, echo=settings.DATABASE_ECHO
        )

    def setup_db(self, is_drop_table: bool = False):
        with self._engine.begin() as conn:
            if is_drop_table:
                # Drop all tables in the database
                Base.metadata.drop_all(conn)

            # Create all tables in the database
            Base.metadata.create_all(conn)

    @property
    def engine(self):
        return self._engine

    @property
    def engine_async(self):
        return self._engine_async


database = DB()
