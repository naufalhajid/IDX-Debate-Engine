from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models.fundamental import Fundamental
from repositories.base import BaseRepository


class FundamentalRepository(BaseRepository[Fundamental]):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session, model_class=Fundamental)

    async def get_by_stock_ticker(self, ticker: str) -> Fundamental | None:
        stmt = (
            select(Fundamental)
            .options(
                selectinload(Fundamental.stat),
                selectinload(Fundamental.current_valuation),
                selectinload(Fundamental.per_share),
                selectinload(Fundamental.solvency),
                selectinload(Fundamental.management_effectiveness),
                selectinload(Fundamental.profitability),
                selectinload(Fundamental.growth),
                selectinload(Fundamental.dividend),
                selectinload(Fundamental.market_rank),
                selectinload(Fundamental.income_statement),
                selectinload(Fundamental.balance_sheet),
                selectinload(Fundamental.cash_flow_statement),
                selectinload(Fundamental.price_performance),
            )
            .where(Fundamental.stock_ticker == ticker)
            .order_by(Fundamental.created_at.desc())
        )
        result = await self.session.scalars(stmt)
        return result.first()
