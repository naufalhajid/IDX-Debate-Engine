from contextlib import contextmanager, asynccontextmanager

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db import DB

engine = DB().engine
engine_async = DB().engine_async

SessionFactory = sessionmaker(bind=engine)
AsyncSessionFactory = async_sessionmaker(bind=engine_async, expire_on_commit=False)

Session = scoped_session(SessionFactory)


@contextmanager
def get_session():
    session = Session()
    try:
        yield session
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def get_async_session():
    session: AsyncSession = AsyncSessionFactory()
    try:
        yield session
        await session.commit()
    except SQLAlchemyError:
        await session.rollback()
        raise
    finally:
        await session.close()
