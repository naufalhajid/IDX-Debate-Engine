from typing import Generic, Optional, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import BaseModel


ModelType = TypeVar("ModelType", bound=BaseModel)


class BaseRepository(Generic[ModelType]):
    def __init__(self, session: AsyncSession, model_class: Type[ModelType]):
        self._session = session
        self._model_class = model_class

    @property
    def session(self) -> AsyncSession:
        return self._session

    @property
    def model_class(self) -> Type[ModelType]:
        return self._model_class

    async def get(self, id_: int) -> Optional[ModelType]:
        stmt = select(self._model_class).where(self._model_class.id == id_)
        result = await self._session.scalars(stmt)
        return result.first()

    async def list(self) -> list[ModelType]:
        stmt = select(self._model_class)
        result = await self._session.scalars(stmt)
        return list(result)

    async def add(self, instance: ModelType) -> ModelType:
        self._session.add(instance)
        return instance

    async def delete(self, instance: ModelType) -> None:
        await self._session.delete(instance)
