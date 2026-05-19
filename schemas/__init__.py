from typing import Any

from pydantic import BaseModel, ConfigDict


class BaseDataClass(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="allow")

    def __init__(self, *args: Any, **data: Any) -> None:
        if args:
            field_names = list(self.__class__.model_fields.keys())
            for name, value in zip(field_names, args):
                if name not in data:
                    data[name] = value
        super().__init__(**data)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_orm(cls, orm_obj: Any):
        return cls.model_validate(orm_obj)
