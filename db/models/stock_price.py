from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship

from db.models import BaseModel, FLOAT


class StockPrice(BaseModel):
    __tablename__ = "stock_prices"

    price: Mapped[FLOAT]
    volume = mapped_column(BigInteger, default=0)
    change: Mapped[FLOAT]
    percentage_change: Mapped[FLOAT]
    average: Mapped[FLOAT]
    close: Mapped[FLOAT]
    high: Mapped[FLOAT]
    low: Mapped[FLOAT]
    open: Mapped[FLOAT]
    ara: Mapped[FLOAT]
    arb: Mapped[FLOAT]
    frequency: Mapped[FLOAT]
    fsell: Mapped[FLOAT]
    fbuy: Mapped[FLOAT]

    stock_ticker = mapped_column(ForeignKey("stocks.ticker"))
    stock: Mapped["Stock"] = relationship(back_populates="stock_prices")
