# AI Agent Customization Guide for IDX Fundamental Analysis

This document helps AI coding agents understand the codebase structure, conventions, and how to work productively in this project.

## Project Overview

**IDX Fundamental Analysis** is a Python-based system for analyzing Indonesian Stock Exchange (IDX) stocks. It retrieves fundamental data, performs sentiment and technical analysis, runs AI-powered debate chambers for investment decisions, and generates swing trade recommendations.

- **Main Deliverable**: `orchestrator.py` automates the complete pipeline (quant filtering → AI debates → ranking)
- **Entry Points**: See [Entry Points](#entry-points-and-how-to-run-them) below
- **Tech Stack**: FastAPI, SQLAlchemy (async), LangGraph, LangChain, Pydantic v2, pytest

## Architecture Overview

```
Data Sources (Providers)
    ↓ [IDX web scrape, StockBit API, yfinance, Google Drive, Gemini LLM]
ETL & Analysis (Builders)
    ↓ [DatabaseBuilder, Analysers (fundamental, sentiment, key_analysis, stock_price)]
Persistence Layer (Repositories)
    ↓ [SQLAlchemy ORM, async queries]
Database
    ↓ [SQLite or PostgreSQL]
Services & Business Logic
    ↓ [DebateChamber, AIAssistant, FairValueCalculator, etc.]
API & Output
    ↓ [FastAPI routers, JSON exports, markdown reports]
```

### Core Modules

| Module | Purpose |
|--------|---------|
| `providers/` | Data source abstractions: `idx.py` (web scrape), `stockbit.py` (API), `yfinance.py`, `gemini.py` (LLM), `google_drive_service.py` |
| `builders/` | ETL processing: `DatabaseBuilder` orchestrates; `analysers/` contains domain-specific logic |
| `repositories/` | Query layer: `BaseRepository[T]` + model-specific subclasses (Stock, Fundamental, Sentiment, etc.) |
| `services/` | Business logic: `DebateChamber` (multi-agent decisions), `AIAssistant` (LLM interface), fair value calculations, trade debate |
| `core/` | System-level: `settings.py` (config), `regime.py` (market regime), `portfolio_optimizer.py`, `budget.py` (API cost tracking), `dependency_validator.py` |
| `db/` | ORM: SQLAlchemy models in `models/` (Stock, Fundamental, StockPrice, Sentiment, KeyAnalysis), async session management in `session.py` |
| `schemas/` | Pydantic v2 models for API validation and serialization |
| `app/api/` | FastAPI app: routers in `app/api/routers/` (stocks, fundamentals, sentiments, key_analysis, stock_prices, health), dependency injection in `app/api/dependency_injections/` |
| `utils/` | Helpers: `logger_config.py` (Loguru setup), `technicals.py` (TA-Lib), `xlsx_adapter.py`, `serializers.py` |

## Key Patterns & Conventions

### Code Style
- **Naming**: `snake_case` for files/functions, `PascalCase` for classes
- **Async-First**: All I/O uses `async`/`await`; no blocking calls in main loops
- **Imports**: Relative imports within modules, absolute from workspace root

### Architecture Patterns

#### 1. **Builder Pattern** (`builders/builder_interface.py`)
```python
class BuilderInterface:
    async def build(self) -> BuildResult:
        """Transform and persist data."""
```
Implementations: `DatabaseBuilder`, analysers that inherit from a base analyser pattern.

#### 2. **Repository Pattern** (`repositories/base.py`)
```python
class BaseRepository(Generic[ModelType]):
    async def create(self, obj_in: CreateSchema) -> ModelType
    async def read(self, id: Any) -> Optional[ModelType]
    async def list(self, **filters) -> List[ModelType]
    async def update(self, id: Any, obj_in: UpdateSchema) -> ModelType
    async def delete(self, id: Any) -> None
```
All model-specific repos inherit from `BaseRepository`. Queries are async via `AsyncSession`.

#### 3. **Schema Validation** (`schemas/`)
- Input/output contracts via Pydantic `BaseModel`
- Models for all domain entities (Fundamental, Stock, Sentiment, KeyAnalysis, StockPrice)
- Special models: `schemas/debate.py` for debate I/O

#### 4. **Dependency Injection** (`app/api/dependency_injections/`)
- Lightweight manual injection: services receive dependencies as constructor params
- No heavy DI framework; factories and shared session management

#### 5. **Database Relationships** (`db/models/`)
Example: A Stock has many Fundamentals, StockPrices, Sentiments, KeyAnalysis records (one-to-many)
```python
# Relationship defined in Stock model:
fundamentals = relationship("Fundamental", back_populates="stock")
```

### Async Patterns
- All endpoints in `app/api/routers/` are async
- `AsyncSession` from SQLAlchemy; always use `async with` for session context
- Providers wrap blocking calls (web scrape, API calls) with retry logic (`tenacity`)

### Error Handling
- Tenacity retry logic with exponential backoff: `@retry(retry=retry_if_exception_type(RequestException))`
- Loguru for structured logging; log levels configured in `Settings`
- Custom exceptions in service layer (e.g., `InvalidDebateStateError`)

## Entry Points and How to Run Them

### 1. **Full Analysis Pipeline** (`orchestrator.py`)
```bash
python orchestrator.py
```
- Automated end-to-end: quant filtering → AI debate → scoring → final report
- Outputs: `output/full_batch_results.json`, `output/TOP_3_SWING_TRADES.md`, individual debate JSON files

### 2. **Main ETL Script** (`main.py`)
```bash
python main.py [-f] [-o {excel|spreadsheet}]
```
- `-f`: Fetch full stock list from IDX (default: first page only)
- `-o`: Output to Excel file or Google Sheet
- Runs: IDX scrape → StockBit/yfinance data → analysis → persistence

### 3. **FastAPI Server** (`run_api.py`)
```bash
python run_api.py
```
- Starts dev server on `http://127.0.0.1:8000` with hot reload
- Swagger UI: `http://127.0.0.1:8000/docs`
- Routers in `app/api/routers/`: `/stocks`, `/fundamentals`, `/sentiments`, `/key_analysis`, `/stock_prices`, `/health`

### 4. **Quantitative Filter** (`run_quant_filter.py`)
```bash
python run_quant_filter.py
```
- Swing-trade candidate screening engine
- Outputs: `output/top10_candidates.json` with ranked tickers

### 5. **Debate Chamber** (`run_debate.py`)
```bash
python run_debate.py --tickers BBRI BBCA --output-dir output/debates
```
- Multi-agent AI debate on specific tickers
- Outputs: JSON debate transcripts to `output/debates/`

### 6. **Sector Cache Builder** (`build_sector_cache.py`)
```bash
python build_sector_cache.py
```
- Pre-computes sector classification cache
- Outputs: `output/sector_cache.json`

### 7. **Testing**
```bash
pytest                  # Run all tests
pytest -v              # Verbose output
pytest tests/test_historical_scorer.py  # Single file
pytest -k test_name    # Single test
```
- Fixtures in `conftest.py` (async factories, sample models)
- Config in `pyproject.toml`: `pythonpath=["."]`, `testpaths=["tests"]`

## Database & ORM

### SQLAlchemy Setup
- **Models**: `db/models/` (Stock, Fundamental, StockPrice, Sentiment, KeyAnalysis, + more in __init__.py)
- **Async Session**: `db/session.py` provides `AsyncSession` factory
- **Relationships**: One-to-many (Stock → children), configured with `back_populates`

### Common Tasks

**Add a new model:**
1. Create class in `db/models/new_model.py` inheriting from `Base`
2. Define columns, relationships, and constraints
3. Add import to `db/models/__init__.py`
4. Create schema in `schemas/new_model.py` (Pydantic)
5. Create repository: `repositories/new_model_repository.py` extending `BaseRepository`
6. Create router: `app/api/routers/new_model.py` using repository

**Query data:**
```python
# In service or router
from repositories.stock_repository import StockRepository
repo = StockRepository(session)
stocks = await repo.list(sector="Finance")
stock = await repo.read(ticker)
```

**Update data:**
```python
updated = await repo.update(stock_id, StockUpdateSchema(price=100.5))
```

## Configuration & Environment

### Settings Management (`core/settings.py`)
- Loads from `.env` file using Pydantic `BaseSettings`
- Falls back to defaults or `os.getenv()` for runtime overrides

### Critical Environment Variables
| Variable | Purpose | Default/Example |
|----------|---------|---|
| `GEMINI_API_KEY` | Gemini LLM access | *(required)* |
| `GEMINI_FLASH_MODEL` | Fast model for non-critical tasks | `gemini-2.0-flash` |
| `GEMINI_PRO_MODEL` | High-quality model for debates | `gemini-2.0-pro` |
| `DATABASE_TYPE` | `sqlite` or `postgres` | `sqlite` |
| `DATABASE_URL` | SQLite file path or Postgres DSN | `sqlite:///db.sqlite3` |
| `GOOGLE_SERVICE_ACCOUNT` | Google API credentials (JSON) | *(optional)* |
| `GOOGLE_DRIVE_EMAILS` | Share spreadsheets with these emails | `["user@gmail.com"]` |
| `CONVICTION_WEIGHT_CONFIDENCE` | Scoring weight for AI confidence | `0.4` |
| `CONVICTION_WEIGHT_RR_RATIO` | Scoring weight for risk/reward ratio | `0.6` |
| `MAX_CONCURRENT_DEBATES` | Parallel debate limit | `3` |
| `GEMINI_RPM_LIMIT` | Gemini requests per minute | `100` |
| `BATCH_DELAY_SECONDS` | Delay between batch requests | `1` |
| `LOG_LEVEL` | Console log level | `INFO` |
| `LOG_FILE_ACCESS_LEVEL` | File log level | `DEBUG` |

### Adding New Settings
1. Add property to `Settings` class in `core/settings.py`
2. Use `Field(default=..., description="...")` for clarity
3. Reference via `settings.your_variable` (singleton injected in routers)

## API Patterns

### Routers
Each router file corresponds to a domain model:
- `app/api/routers/stocks.py` — CRUD on stocks
- `app/api/routers/fundamentals.py` — Fundamentals data
- Similar for sentiments, key_analysis, stock_prices, health checks

All routers are async endpoints using FastAPI conventions.

### Dependency Injection (FastAPI)
```python
from fastapi import Depends
from app.api.dependency_injections.session import get_async_session

async def get_stocks(session: AsyncSession = Depends(get_async_session)):
    repo = StockRepository(session)
    return await repo.list()
```

### CORS & Middleware
- Configured via `Settings.MIDDLEWARE_CORS`
- Applied in lifespan context manager (`core/registrar.py`)

## Services & Business Logic

### DebateChamber (`services/debate_chamber.py`)
Multi-agent system for investment decisions:
- Agents: Bull, Bear, Analyst (LangGraph-based state machine)
- Iterative debate with consensus checking
- Anti-groupthink logic to bypass consensus if conviction score is decisive

### AIAssistant (`services/ai_assistant.py`)
- LLM wrapper for Gemini
- Handles token limits, rate limiting via budget tracker
- Formats prompts, parses responses

### FairValueCalculator (`services/fair_value_calculator.py`)
- DCF, P/E multiple, and other valuation methods
- Harmonizes multiple estimates into consensus fair value

### StockBit API Client (`services/stockbit_api_client.py`)
- Fetch fundamental metrics from StockBit
- Token-based auth, rate-limited

## Testing

### Test Structure
- **Location**: `tests/`
- **Fixture Factory**: `conftest.py` provides factory functions for creating test models
- **Async Tests**: Use `pytest-asyncio` (configured in `pyproject.toml`)

### Example Test
```python
async def test_stock_repository_read(stock_factory):
    stock = await stock_factory()  # Create test stock
    repo = StockRepository(session)
    result = await repo.read(stock.id)
    assert result.ticker == stock.ticker
```

### Running Tests
```bash
uv run pytest                      # All tests
uv run pytest -v                  # Verbose
uv run pytest --co                # List tests
uv run pytest -k "test_name"      # Specific test
uv run pytest --tb=short tests/   # Custom output
```

## Common Development Tasks

### Task: Add a New Analysis Type
1. Create analyser in `builders/analysers/new_analyser.py` inheriting from analyser base
2. Implement `async def analyze(stock_data)` → returns `NewAnalysisSchema`
3. Add to `DatabaseBuilder.build()` orchestration
4. Create DB model in `db/models/new_analysis.py`
5. Create repository and API router (follow existing patterns)

### Task: Modify Debate Logic
1. Edit `services/debate_chamber.py` — state definitions, agent prompts, consensus logic
2. Update schema in `schemas/debate.py` if changing I/O structure
3. Test with `python run_debate.py --tickers BBRI`

### Task: Add API Endpoint
1. Create/update router in `app/api/routers/`
2. Use `AsyncSession` dependency for data access
3. Return Pydantic schema for response
4. Document with FastAPI docstrings; auto-exposed in Swagger UI

### Task: Tune Scoring & Conviction Weights
- Edit `CONVICTION_WEIGHT_*` in `.env` or `core/settings.py`
- Regime detection in `core/regime.py` affects thresholds
- Historical scorer in `core/historical_scorer.py` adapts based on past performance

## Important Configuration Files

| File | Purpose |
|------|---------|
| `pyproject.toml` | Dependencies, tool config (pytest, ruff), Python version constraint |
| `.env.example` | Template for environment variables *(create .env from this)* |
| `core/settings.py` | Central configuration loading |
| `db/session.py` | SQLAlchemy async session factory |
| `app/api/main.py` | FastAPI app instantiation |
| `core/registrar.py` | Lifespan management (DB init, CORS setup) |

## Debugging & Troubleshooting

### Issue: "Database not initialized"
- Ensure `DATABASE_URL` is set in `.env`
- Run `main.py` or `orchestrator.py` once to bootstrap DB

### Issue: Gemini API rate limit / budget exceeded
- Check `MAX_CONCURRENT_DEBATES`, `GEMINI_RPM_LIMIT` in `.env`
- Budget tracker logs token usage; see logs for details

### Issue: Web scraper (IDX provider) fails
- IDX provider uses undetected-chromedriver; ensure Chrome/Chromium installed
- Check logs for Selenium errors; retry logic may auto-recover

### Issue: Async warnings or deadlocks
- All I/O must use async: repos, API calls, file I/O
- Never call blocking functions in async context (use `loop.run_in_executor()` if needed)

### Logging
- Configure via `LOG_LEVEL`, `LOG_FILE_ACCESS_LEVEL` in `.env`
- Loguru writes to files in `logs/` directory
- Use `from utils.logger_config import logger` in any module

## Quick Reference: File Locations

```
src structure:
  core/              → System-level config, regime, optimization
  db/                → ORM models, session factory
  providers/         → External data sources
  builders/          → ETL and analysis
  repositories/      → Query layer (SQL abstraction)
  services/          → Business logic (debate, AI, fair value)
  schemas/           → Pydantic models
  app/api/           → FastAPI routers and DI
  utils/             → Logging, helpers, technicals
  tests/             → pytest test suite
  output/            → Results (JSON, markdown, sector cache)
  scratch/           → Experimental / temp scripts
```

## Contributing

- Follow the existing architecture patterns (builder, repository, service, router)
- Use async/await throughout
- Write tests for new logic in `tests/`
- Log important decisions and errors via Loguru
- Keep type hints consistent (PEP 484)
- Validate inputs via Pydantic schemas

---

**Last Updated**: May 2026  
**Python Version**: 3.12–3.14  
**Package Manager**: UV
