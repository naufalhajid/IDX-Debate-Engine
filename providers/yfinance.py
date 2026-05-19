import yfinance as yf
from pyrate_limiter import Limiter, RequestRate, Duration, MemoryQueueBucket
from requests import Session
from requests_cache import CacheMixin, SQLiteCache
from requests_ratelimiter import LimiterMixin

from core.failure_taxonomy import classify_exception
from utils.logger_config import logger


class CachedLimiterSession(CacheMixin, LimiterMixin, Session):
    pass


class YFinance:
    def __init__(self):
        self.yf = yf
        self.session = CachedLimiterSession(
            limiter=Limiter(
                RequestRate(2, Duration.SECOND * 5)
            ),  # max 2 requests per 5 seconds
            bucket_class=MemoryQueueBucket,
            backend=SQLiteCache("yfinance.cache"),
        )
        self.session.headers["User-agent"] = "my-program/1.0"

    def close_price(self, stock):
        try:
            ticker = self.yf.Ticker(stock.ticker, session=self.session)
            hist = ticker.history(period="1d")
            return hist["Close"].iloc[-1]  # Get the last close price
        except Exception as exc:
            failure = classify_exception(exc, source="yfinance")
            logger.error(f"[YFinance] close_price failed: {failure.model_dump()}")
            raise
