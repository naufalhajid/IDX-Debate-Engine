from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.sentiment import Sentiment
from repositories.base import BaseRepository


class SentimentRepository(BaseRepository[Sentiment]):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session, model_class=Sentiment)

    async def list_by_stock_ticker(self, ticker: str) -> list[Sentiment]:
        stmt = select(Sentiment).where(Sentiment.stock_ticker == ticker)
        result = await self.session.scalars(stmt)
        return list(result)
