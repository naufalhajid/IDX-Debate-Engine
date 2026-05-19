from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.key_analysis import KeyAnalysis
from repositories.base import BaseRepository


class KeyAnalysisRepository(BaseRepository[KeyAnalysis]):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session, model_class=KeyAnalysis)

    async def get_by_stock_ticker(self, ticker: str) -> KeyAnalysis | None:
        stmt = (
            select(KeyAnalysis)
            .where(KeyAnalysis.stock_ticker == ticker)
            .order_by(KeyAnalysis.created_at.desc())
        )

        result = await self.session.scalars(stmt)
        return result.first()
