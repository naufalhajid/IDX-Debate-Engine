from __future__ import annotations

from types import SimpleNamespace

from providers import stockbit as stockbit_module
from providers.stockbit import StockBit
from schemas.stock import Stock


def _provider(stocks: list[Stock]) -> StockBit:
    provider = object.__new__(StockBit)
    provider.stocks = stocks
    provider.base_url = "https://exodus.stockbit.com"
    provider.stockbit_api_client = SimpleNamespace(reauthenticate=lambda: None)
    return provider


def _post(stream_id: str, *, is_verified: bool = False) -> dict:
    return {
        "stream_id": stream_id,
        "id": f"id-{stream_id}",
        "post_id": f"post-{stream_id}",
        "content": f"{stream_id} stockbit post",
        "created_at": "2026-05-27T09:00:00+07:00",
        "user": {"is_verified": is_verified},
    }


def test_with_stream_data_fetches_both_categories(monkeypatch) -> None:
    stock = Stock(ticker="BBCA")
    provider = _provider([stock])
    requested_categories: list[str] = []

    monkeypatch.setattr(
        provider,
        "stream_pinned_by_stock",
        lambda stock: {"data": []},
    )

    def fake_stream_by_stock(stock, category="STREAM_CATEGORY_IDEAS") -> dict:
        requested_categories.append(category)
        return {"data": {"stream": []}}

    monkeypatch.setattr(provider, "stream_by_stock", fake_stream_by_stock)

    provider.with_stream_data()

    assert "STREAM_CATEGORY_IDEAS" in requested_categories
    assert "STREAM_CATEGORY_NEWS" in requested_categories
    assert len(requested_categories) == 2


def test_with_stream_data_verified_weight_applied(monkeypatch) -> None:
    stock = Stock(ticker="BBCA")
    provider = _provider([stock])

    monkeypatch.setattr(
        provider,
        "stream_pinned_by_stock",
        lambda stock: {"data": []},
    )

    def fake_stream_by_stock(stock, category="STREAM_CATEGORY_IDEAS") -> dict:
        if category == "STREAM_CATEGORY_IDEAS":
            return {
                "data": {
                    "stream": [
                        _post("verified", is_verified=True),
                        _post("retail", is_verified=False),
                    ]
                }
            }
        return {"data": {"stream": []}}

    monkeypatch.setattr(provider, "stream_by_stock", fake_stream_by_stock)

    posts = provider._safe_fetch_stream_data(stock)

    weights = {post["stream_id"]: post["_verified_weight"] for post in posts}
    assert weights == {"verified": 1.5, "retail": 1.0}
    assert len(posts) == 2


def test_with_stream_data_category_failure_partial_result(monkeypatch) -> None:
    stock = Stock(ticker="BBCA")
    provider = _provider([stock])
    warnings: list[str] = []

    monkeypatch.setattr(
        stockbit_module,
        "logger",
        SimpleNamespace(
            warning=lambda message: warnings.append(str(message)),
            debug=lambda *args, **kwargs: None,
            info=lambda *args, **kwargs: None,
        ),
    )
    monkeypatch.setattr(
        provider,
        "stream_pinned_by_stock",
        lambda stock: {"data": []},
    )

    def fake_stream_by_stock(stock, category="STREAM_CATEGORY_IDEAS") -> dict:
        if category == "STREAM_CATEGORY_NEWS":
            raise TimeoutError("news timeout")
        return {"data": {"stream": [_post(f"idea-{i}") for i in range(10)]}}

    monkeypatch.setattr(provider, "stream_by_stock", fake_stream_by_stock)

    posts = provider._safe_fetch_stream_data(stock)

    assert len(posts) == 10
    assert [post["stream_id"] for post in posts] == [f"idea-{i}" for i in range(10)]
    assert any("STREAM_CATEGORY_NEWS" in warning for warning in warnings)


class _ReprBomb:
    """Objek dengan ticker valid tapi __str__ meledak (cermin Stock ter-enrich
    yang repr pydantic-nya siklik -> RecursionError)."""

    ticker = "BBCA"

    def __str__(self) -> str:
        raise AssertionError("str(stock) tidak boleh dievaluasi saat ticker ada")


def test_ticker_does_not_eagerly_str_the_stock() -> None:
    # Regresi: getattr(stock, "ticker", str(stock)) mengevaluasi default secara
    # EAGER -> str() model siklik memutus SEMUA stream fetch di scan.
    assert StockBit._ticker(_ReprBomb()) == "BBCA"


def test_ticker_falls_back_to_str_for_plain_strings() -> None:
    assert StockBit._ticker("BBCA") == "BBCA"
