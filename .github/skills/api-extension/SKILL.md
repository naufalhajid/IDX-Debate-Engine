---
name: api-extension
description: Add or modify database-backed FastAPI API resources in IDX Fundamental Analysis. Use when Codex is asked to create or change an ORM model, Pydantic schema, repository, router, dependency injection path, endpoint, or API test under app/api, db/models, schemas, repositories, or tests.
---

# API Extension

## Orientation

Follow the existing model-schema-repository-router pattern. The API layer is intentionally simple: FastAPI routers depend on `get_db`, instantiate a repository, fetch async SQLAlchemy models, and return Pydantic schemas.

Start by copying the closest existing resource. For simple ticker-based read endpoints, `fundamentals`, `sentiments`, `key_analysis`, and `stock_prices` are better templates than inventing a new abstraction.

## Resource Checklist

When adding a new database-backed resource, update only the pieces that apply:

1. Add the SQLAlchemy model in `db/models/<resource>.py`.
2. Import the model in `db/__init__.py` so `Base.metadata.create_all()` sees it.
3. Add or update relationships on `db/models/stock.py` when the resource belongs to a stock.
4. Add the Pydantic schema in `schemas/<resource>.py`.
5. Add a repository in `repositories/<resource>_repository.py` extending `BaseRepository`.
6. Add a router in `app/api/routers/<resource>.py`.
7. Register the router in `app/api/routers/__init__.py`.
8. Add focused tests under `tests/` when behavior is new or non-trivial.

## Local Patterns

Use async SQLAlchemy queries through `AsyncSession`:

```python
stmt = select(Model).where(Model.stock_ticker == ticker).order_by(Model.created_at.desc())
result = await self.session.scalars(stmt)
return result.first()
```

Use router dependencies like existing endpoints:

```python
@router.get("/{ticker}/latest", response_model=ResourceSchema)
async def get_latest_resource(ticker: str, db: AsyncSession = Depends(get_db)) -> ResourceSchema:
    repository = ResourceRepository(db)
    resource = await repository.get_by_stock_ticker(ticker)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return ResourceSchema.from_orm(resource)
```

Use `schemas.BaseDataClass` for Pydantic models unless the surrounding code already uses a more specific base.

## Model Guidance

Prefer the type aliases in `db/models/__init__.py` for consistency: `VARCHAR`, `FLOAT`, `INT_PK`, `TIMESTAMP`, and `UPDATED_TIMESTAMP`.

For stock-owned tables:

- Store the ticker using the existing project convention if similar tables use `stock_ticker`.
- Add `relationship(back_populates=...)` on both sides when ORM navigation is needed.
- Use `selectinload` in repositories when returning nested related data.

This project does not currently use migrations. Schema creation happens through `database.setup_db()` and `Base.metadata.create_all()`, so note any destructive database implications before changing existing columns.

## Error Handling And Responses

- Return `404` for missing ticker/resource data, matching existing routers.
- Keep response models explicit.
- Avoid leaking provider errors or raw exception traces through API responses.
- Keep endpoint names and prefixes plural and kebab-case where existing routers do so, such as `/stock-prices` and `/key-analyses`.

## Validation

Run targeted tests first, then lint:

```bash
uv run pytest tests/
uv run ruff check .
```

For manual API checks, start the app only when needed:

```bash
uv run python run_api.py
```

Then inspect `http://127.0.0.1:8000/docs`.
