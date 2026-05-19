from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import mapped_column, Mapped, relationship

from db.models import BaseModel, VARCHAR, FLOAT


class Sentiment(BaseModel):
    __tablename__ = "sentiments"

    content: Mapped[VARCHAR]
    rate: Mapped[FLOAT]
    category: Mapped[VARCHAR]
    posted_at = mapped_column(DateTime)

    stock_ticker = mapped_column(ForeignKey("stocks.ticker"))
    stock: Mapped["Stock"] = relationship(back_populates="sentiments")
