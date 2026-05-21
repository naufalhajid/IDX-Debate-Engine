from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from db.session import AsyncSessionFactory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionFactory() as session:
        yield session
