from __future__ import annotations

from types import SimpleNamespace

from providers.idx import IDX


class _Table:
    def __init__(self) -> None:
        self._columns = {
            1: [SimpleNamespace(text=" bbca.jk "), SimpleNamespace(text="../escape")],
            2: [SimpleNamespace(text="Bank Central Asia"), SimpleNamespace(text="Bad")],
            3: [SimpleNamespace(text="31-05-2000"), SimpleNamespace(text="-")],
            4: [SimpleNamespace(text="1,000"), SimpleNamespace(text="1")],
            5: [SimpleNamespace(text=""), SimpleNamespace(text="")],
        }

    def find_elements(self, _by, xpath: str):
        for column, values in self._columns.items():
            if f"td[{column}]" in xpath:
                return values
        raise AssertionError(f"unexpected XPath: {xpath}")


class _Driver:
    def __init__(self, table: _Table) -> None:
        self.table = table
        self.urls: list[str] = []

    def get(self, url: str) -> None:
        self.urls.append(url)

    def find_element(self, _by, _xpath: str) -> _Table:
        return self.table


def test_idx_scrape_skips_invalid_ticker_and_canonicalizes_valid_row() -> None:
    provider = object.__new__(IDX)
    provider.base_url = "https://idx.co.id"
    provider.is_full_retrieve = False
    provider.is_second_page = False
    provider._own_driver = False
    provider.driver = _Driver(_Table())
    provider._wait_for_table = lambda _url: None

    stocks = provider.stocks()

    assert [stock.ticker for stock in stocks] == ["BBCA"]
    assert provider.driver.urls == [
        "https://idx.co.id/id/data-pasar/data-saham/daftar-saham/"
    ]
