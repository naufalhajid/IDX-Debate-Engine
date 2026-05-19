from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.stock_price import StockPrice
from repositories.base import BaseRepository


class StockPriceRepository(BaseRepository[StockPrice]):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session, model_class=StockPrice)

    async def list_by_stock_ticker(self, ticker: str) -> list[StockPrice]:
        stmt = select(StockPrice).where(StockPrice.stock_ticker == ticker)
        result = await self.session.scalars(stmt)
        return list(result)
