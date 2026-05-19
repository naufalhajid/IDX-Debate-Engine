from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import mapped_column, Mapped, relationship

from db import BaseModel, FLOAT


class KeyAnalysis(BaseModel):
    __tablename__ = "key_analyses"

    normal_price: Mapped[FLOAT]
    price_to_equity_discount: Mapped[FLOAT]
    relative_pe_ratio_ttm: Mapped[FLOAT]
    eps_growth: Mapped[FLOAT]
    debt_to_total_assets_ratio: Mapped[FLOAT]
    liquidity_differential: Mapped[FLOAT]
    cce: Mapped[FLOAT]
    operating_efficiency: Mapped[FLOAT]
    dividend_payout_efficiency: Mapped[FLOAT]
    yearly_price_change: Mapped[FLOAT]
    composite_rank: Mapped[FLOAT]
    net_debt_to_equity_ratio: Mapped[FLOAT]

    stock_ticker = mapped_column(String, ForeignKey("stocks.ticker"))
    stock: Mapped["Stock"] = relationship(back_populates="key_analyses")
