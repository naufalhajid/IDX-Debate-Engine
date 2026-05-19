from typing import TYPE_CHECKING

from sqlalchemy.orm import mapped_column, Mapped, relationship

from db.models import BaseModel, VARCHAR, FLOAT

if TYPE_CHECKING:
    from db.models.fundamental import Fundamental
    from db.models.key_analysis import KeyAnalysis
    from db.models.sentiment import Sentiment
    from db.models.stock_price import StockPrice


class Stock(BaseModel):
    __tablename__ = "stocks"

    ticker: Mapped[VARCHAR] = mapped_column(index=True, nullable=False, unique=True)
    name: Mapped[VARCHAR]
    ipo_date: Mapped[VARCHAR]
    note: Mapped[VARCHAR]
    market_cap: Mapped[FLOAT]
    home_page: Mapped[VARCHAR]

    stock_prices: Mapped[list["StockPrice"]] = relationship(back_populates="stock")
    fundamentals: Mapped[list["Fundamental"]] = relationship(back_populates="stock")
    sentiments: Mapped[list["Sentiment"]] = relationship(back_populates="stock")
    key_analyses: Mapped[list["KeyAnalysis"]] = relationship(back_populates="stock")
