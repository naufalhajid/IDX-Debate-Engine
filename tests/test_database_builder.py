import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from builders.database_builder import DatabaseBuilder
from schemas.stock import Stock as StockSchema
from schemas.stock_price import StockPrice


@pytest.fixture
def mock_stock():
    return StockSchema(
        ticker="TLKM",
        name="Telkom Indonesia",
        ipo_date="1995-11-14",
        note="Top Telco",
        market_cap=300000.0,
        home_page="https://telkom.co.id",
    )


@pytest.mark.asyncio
async def test_insert_stock(mock_stock):
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Mock get_async_session context manager
    mock_context = MagicMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = None

    with patch(
        "builders.database_builder.get_async_session", return_value=mock_context
    ):
        builder = DatabaseBuilder(stocks=[mock_stock])
        await builder.insert_stock()

        # Verify it added the stock to session
        assert mock_session.add.call_count == 1
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.ticker == "TLKM"
        assert added_obj.name == "Telkom Indonesia"


@pytest.mark.asyncio
async def test_update_or_insert_stock_new(mock_stock):
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Mock scalars to return None (no existing stock)
    mock_result = MagicMock()
    mock_result.one_or_none.return_value = None
    mock_session.scalars.return_value = mock_result

    mock_context = MagicMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = None

    with patch(
        "builders.database_builder.get_async_session", return_value=mock_context
    ):
        builder = DatabaseBuilder(stocks=[mock_stock])
        await builder.update_or_insert_stock()

        # Verify query and insert
        assert mock_session.scalars.call_count == 1
        assert mock_session.add.call_count == 1
        added_obj = mock_session.add.call_args[0][0]
        assert added_obj.ticker == "TLKM"


@pytest.mark.asyncio
async def test_update_or_insert_stock_existing(mock_stock):
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    # Mock existing stock model
    existing_stock = MagicMock()

    mock_result = MagicMock()
    mock_result.one_or_none.return_value = existing_stock
    mock_session.scalars.return_value = mock_result

    mock_context = MagicMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = None

    with patch(
        "builders.database_builder.get_async_session", return_value=mock_context
    ):
        builder = DatabaseBuilder(stocks=[mock_stock])
        await builder.update_or_insert_stock()

        # Verify update and NO insert
        assert mock_session.scalars.call_count == 1
        assert mock_session.add.call_count == 0
        assert existing_stock.market_cap == mock_stock.market_cap
        assert existing_stock.note == mock_stock.note


@pytest.mark.asyncio
async def test_insert_stock_price(mock_stock):
    mock_session = AsyncMock()
    mock_session.add = MagicMock()

    mock_stock.stock_price = StockPrice(price=4000.0, volume=1000000.0)

    mock_context = MagicMock()
    mock_context.__aenter__.return_value = mock_session
    mock_context.__aexit__.return_value = None

    with patch(
        "builders.database_builder.get_async_session", return_value=mock_context
    ):
        builder = DatabaseBuilder(stocks=[mock_stock])
        await builder.insert_stock_price()

        assert mock_session.add.call_count == 1
        added_price = mock_session.add.call_args[0][0]
        assert added_price.stock_ticker == "TLKM"
        assert added_price.price == 4000.0
