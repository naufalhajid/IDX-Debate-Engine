from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from typing_extensions import Annotated

INT_PK = Annotated[int, mapped_column(primary_key=True)]
TIMESTAMP = Annotated[
    datetime,
    mapped_column(nullable=False, server_default=func.CURRENT_TIMESTAMP()),
]
UPDATED_TIMESTAMP = Annotated[
    datetime,
    mapped_column(
        nullable=False,
        server_default=func.CURRENT_TIMESTAMP(),
        onupdate=func.CURRENT_TIMESTAMP(),
    ),
]
VARCHAR = Annotated[str, mapped_column(default="")]
FLOAT = Annotated[float, mapped_column(default=0.0)]


class Base(DeclarativeBase):
    pass


class BaseModel(Base):
    __abstract__ = True  # This tells SQLAlchemy not to create a table for this class

    id: Mapped[INT_PK]
    created_at: Mapped[TIMESTAMP]
    updated_at: Mapped[UPDATED_TIMESTAMP]
