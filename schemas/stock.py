from datetime import datetime
from typing import List, Optional

from pydantic import Field

from schemas import BaseDataClass
from schemas.fundamental import Fundamental
from schemas.key_analysis import KeyAnalysis
from schemas.sentiment import Sentiment
from schemas.stock_price import StockPrice


class Stock(BaseDataClass):
    ticker: str
    name: str = ""
    ipo_date: str = ""
    note: str = ""
    market_cap: float = 0.0
    home_page: str = ""
    stock_price: Optional[StockPrice] = Field(default_factory=StockPrice)
    sentiment: Optional[List[Sentiment]] = Field(default_factory=lambda: [Sentiment()])
    fundamental: Optional[Fundamental] = Field(default_factory=Fundamental)
    key_analysis: Optional[KeyAnalysis] = Field(default_factory=KeyAnalysis)
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()

    @classmethod
    def from_orm(cls, orm_obj):
        stock_prices = getattr(orm_obj, "stock_prices", []) or []
        fundamentals = getattr(orm_obj, "fundamentals", []) or []
        key_analyses = getattr(orm_obj, "key_analyses", []) or []
        sentiments = getattr(orm_obj, "sentiments", []) or []

        latest_price = stock_prices[-1] if stock_prices else None
        latest_fundamental = fundamentals[-1] if fundamentals else None
        latest_key_analysis = key_analyses[-1] if key_analyses else None

        return cls(
            ticker=orm_obj.ticker,
            name=getattr(orm_obj, "name", ""),
            ipo_date=getattr(orm_obj, "ipo_date", ""),
            note=getattr(orm_obj, "note", ""),
            market_cap=getattr(orm_obj, "market_cap", 0.0),
            home_page=getattr(orm_obj, "home_page", ""),
            stock_price=StockPrice.from_orm(latest_price)
            if latest_price is not None
            else StockPrice(),
            sentiment=[Sentiment.from_orm(s) for s in sentiments],
            fundamental=Fundamental.from_orm(latest_fundamental)
            if latest_fundamental is not None
            else Fundamental(),
            key_analysis=KeyAnalysis.from_orm(latest_key_analysis)
            if latest_key_analysis is not None
            else KeyAnalysis(),
            created_at=getattr(orm_obj, "created_at", datetime.now()),
            updated_at=getattr(orm_obj, "updated_at", datetime.now()),
        )
