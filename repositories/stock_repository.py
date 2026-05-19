from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models.stock import Stock
from db.models.fundamental import Fundamental
from repositories.base import BaseRepository


class StockRepository(BaseRepository[Stock]):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session, model_class=Stock)

    async def get_by_ticker(self, ticker: str) -> Stock | None:
        stmt = select(Stock).where(Stock.ticker == ticker)
        result = await self.session.scalars(stmt)
        return result.first()
