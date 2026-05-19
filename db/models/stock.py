from typing import List

from sqlalchemy import select
from sqlalchemy.orm import mapped_column, Mapped, relationship

from db.models import BaseModel, VARCHAR, FLOAT


class Stock(BaseModel):
    __tablename__ = "stocks"

    ticker: Mapped[VARCHAR] = mapped_column(index=True, nullable=False, unique=True)
    name: Mapped[VARCHAR]
    ipo_date: Mapped[VARCHAR]
    note: Mapped[VARCHAR]
    market_cap: Mapped[FLOAT]
    home_page: Mapped[VARCHAR]

    stock_prices: Mapped[List["StockPrice"]] = relationship(back_populates="stock")
    fundamentals: Mapped[List["Fundamental"]] = relationship(back_populates="stock")
    sentiments: Mapped[List["Sentiment"]] = relationship(back_populates="stock")
    key_analyses: Mapped[List["KeyAnalysis"]] = relationship(back_populates="stock")
