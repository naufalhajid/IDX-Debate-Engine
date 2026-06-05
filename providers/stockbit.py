import asyncio
import concurrent.futures
import threading

from datetime import datetime
from dotenv import load_dotenv
from core.settings import get_settings

from schemas.fundamental import (
    Fundamental,
    PerShare,
    Solvency,
    ManagementEffectiveness,
    Profitability,
    Growth,
    Dividend,
    MarketRank,
    IncomeStatement,
    BalanceSheet,
    CashFlowStatement,
    PricePerformance,
    CurrentValuation,
    Stat,
)
from schemas.sentiment import Sentiment
from schemas.stock import Stock
from schemas.stock_price import StockPrice
from utils.helpers import (
    parse_currency_to_float,
    parse_key_statistic_results_item_value,
)
from utils.logger_config import logger

load_dotenv()


class StockBit:
    """
    A class to interact with the StockBit API and fetch key statistics, stock price, and sentiment for stocks.
    """

    def __init__(self, stocks: [Stock]):
        """
        Initializes the StockBit provider with necessary headers and URL.
        """
        logger.info("StockBit provider initialised")
        self.stocks = stocks
        self.base_url = "https://exodus.stockbit.com"
        self.key_statistic = None
        from services.stockbit_api_client import StockbitApiClient

        self.stockbit_api_client = StockbitApiClient()

    def key_statistic_by_stock(self, stock: Stock) -> dict:
        """
        Retrieves key statistics for a given stock by sending a GET request to the API.

        Args:
            stock (Stock): An instance of the Stock class containing the ticker symbol.

        Returns:
            dict: A dictionary containing the key statistics if the request is successful.
            None: If the request fails after retrying or encounters an error.

        Raises:
            requests.exceptions.RequestException: If the request fails due to network issues or invalid URL.

        Side Effects:
            - Logs an error message if the response status code is not 200.
            - Re-authenticates if a 401 Unauthorized status code is received and retries the request up to 3 times.
            - Logs an error message if the request fails due to an exception.
            - Logs an informational message if the request fails after all retries.
        """
        url = f"{self.base_url}/keystats/ratio/v1/{stock.ticker}?year_limit=10"

        return self.stockbit_api_client.get(url)

    def with_fundamental(self):
        """
        Get fundamentals for a list of stocks concurrently.

        Returns:
            Self
        """
        settings = get_settings()
        max_workers = getattr(settings, "STOCKBIT_MAX_WORKERS", 10)

        processed_count = 0
        processed_lock = threading.Lock()

        def safe_fetch(stock):
            nonlocal processed_count
            try:
                key_statistic = self._safe_fetch_key_statistic(stock)
                if key_statistic:
                    stock.fundamental = self._fundamental(stock, key_statistic)

                with processed_lock:
                    processed_count += 1
                    logger.info(
                        f"Processing key statistic for: {stock.ticker} ({processed_count}/{len(self.stocks)})"
                    )
            except Exception as e:
                logger.warning(f"Failed fundamental for {stock.ticker}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(safe_fetch, self.stocks))

        return self

    def _fundamental(self, stock: Stock, key_statistic: dict) -> Fundamental | None:
        """
        Parses the API response data and returns a Fundamental object.

        Args:
            stock (Stock): The Stock object for which the fundamental data is being parsed.

        Returns:
            Fundamental: An object containing parsed fundamental data.
        """

        if not key_statistic:
            return None

        fundamental = Fundamental()
        fundamental.stock = stock

        data = key_statistic.get("data")
        if not data:
            return None

        # Stats
        #
        stat = Stat(
            parse_currency_to_float(data["stats"]["current_share_outstanding"]),
            parse_currency_to_float(data["stats"]["market_cap"]),
            parse_currency_to_float(data["stats"]["enterprise_value"]),
        )
        fundamental.stat = stat
        logger.debug(stat)

        # -- nested object
        closure_fin_items_results = data["closure_fin_items_results"]

        # Current Valuation
        #
        current_valuation_fin_name_results = closure_fin_items_results[0][
            "fin_name_results"
        ]

        current_valuation = CurrentValuation(
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 0
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 1
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 2
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 3
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 4
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 5
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 6
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 7
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 8
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 9
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 10
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 11
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 12
            ),
            parse_key_statistic_results_item_value(
                current_valuation_fin_name_results, 13
            ),
        )
        fundamental.current_valuation = current_valuation
        logger.debug(current_valuation)

        # Per Share
        #
        per_share_fin_name_results = closure_fin_items_results[1]["fin_name_results"]
        per_share = PerShare(
            parse_key_statistic_results_item_value(per_share_fin_name_results, 0),
            parse_key_statistic_results_item_value(per_share_fin_name_results, 1),
            parse_key_statistic_results_item_value(per_share_fin_name_results, 2),
            parse_key_statistic_results_item_value(per_share_fin_name_results, 3),
            parse_key_statistic_results_item_value(per_share_fin_name_results, 4),
            parse_key_statistic_results_item_value(per_share_fin_name_results, 5),
        )
        fundamental.per_share = per_share
        logger.debug(per_share)

        # Solvency
        #
        solvency_fin_name_results = closure_fin_items_results[2]["fin_name_results"]
        solvency = Solvency(
            parse_key_statistic_results_item_value(solvency_fin_name_results, 0),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 1),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 2),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 3),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 4),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 5),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 6),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 7),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 8),
            parse_key_statistic_results_item_value(solvency_fin_name_results, 9),
        )
        fundamental.solvency = solvency
        logger.debug(solvency)

        # Management Effectivieness
        management_effectiveness_fin_name_results = closure_fin_items_results[3][
            "fin_name_results"
        ]
        management_effectiveness = ManagementEffectiveness(
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 0
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 1
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 2
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 3
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 4
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 5
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 6
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 7
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 8
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 9
            ),
            parse_key_statistic_results_item_value(
                management_effectiveness_fin_name_results, 10
            ),
        )
        fundamental.management_effectiveness = management_effectiveness
        logger.debug(management_effectiveness)

        # Profitability
        #
        profitability_fin_name_results = closure_fin_items_results[4][
            "fin_name_results"
        ]
        profitability = Profitability(
            parse_key_statistic_results_item_value(profitability_fin_name_results, 0),
            parse_key_statistic_results_item_value(profitability_fin_name_results, 1),
            parse_key_statistic_results_item_value(profitability_fin_name_results, 2),
        )
        fundamental.profitability = profitability
        logger.debug(profitability)

        # Growth
        #
        growth_fin_name_results = closure_fin_items_results[5]["fin_name_results"]
        growth = Growth(
            parse_key_statistic_results_item_value(growth_fin_name_results, 0),
            parse_key_statistic_results_item_value(growth_fin_name_results, 1),
            parse_key_statistic_results_item_value(growth_fin_name_results, 2),
        )
        fundamental.growth = growth
        logger.debug(growth)

        # Dividend
        #
        dividend_fin_name_results = closure_fin_items_results[6]["fin_name_results"]
        dividend = Dividend(
            parse_key_statistic_results_item_value(dividend_fin_name_results, 0),
            parse_key_statistic_results_item_value(dividend_fin_name_results, 1),
            parse_key_statistic_results_item_value(dividend_fin_name_results, 2),
            parse_key_statistic_results_item_value(dividend_fin_name_results, 3),
            parse_key_statistic_results_item_value(dividend_fin_name_results, 4),
        )
        fundamental.dividend = dividend
        logger.debug(dividend)

        # Market Rank
        #
        market_rank_fin_name_results = closure_fin_items_results[7]["fin_name_results"]
        market_rank = MarketRank(
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 0),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 1),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 2),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 3),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 4),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 5),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 6),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 7),
            parse_key_statistic_results_item_value(market_rank_fin_name_results, 8),
        )
        fundamental.market_rank = market_rank
        logger.debug(market_rank)

        # Income Statement
        #
        income_statement_fin_name_results = closure_fin_items_results[8][
            "fin_name_results"
        ]
        income_statement = IncomeStatement(
            parse_key_statistic_results_item_value(
                income_statement_fin_name_results, 0
            ),
            parse_key_statistic_results_item_value(
                income_statement_fin_name_results, 1
            ),
            parse_key_statistic_results_item_value(
                income_statement_fin_name_results, 2
            ),
            parse_key_statistic_results_item_value(
                income_statement_fin_name_results, 3
            ),
        )
        fundamental.income_statement = income_statement
        logger.debug(income_statement)

        # Balance Sheet
        #
        balance_sheet_fin_name_results = closure_fin_items_results[9][
            "fin_name_results"
        ]
        balance_sheet = BalanceSheet(
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 0),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 1),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 2),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 3),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 4),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 5),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 6),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 7),
            parse_key_statistic_results_item_value(balance_sheet_fin_name_results, 8),
        )
        fundamental.balance_sheet = balance_sheet
        logger.debug(balance_sheet)

        # Cash Flow
        #
        cash_flow_statement_fin_name_results = closure_fin_items_results[10][
            "fin_name_results"
        ]
        cash_flow_statement = CashFlowStatement(
            parse_key_statistic_results_item_value(
                cash_flow_statement_fin_name_results, 0
            ),
            parse_key_statistic_results_item_value(
                cash_flow_statement_fin_name_results, 1
            ),
            parse_key_statistic_results_item_value(
                cash_flow_statement_fin_name_results, 2
            ),
            parse_key_statistic_results_item_value(
                cash_flow_statement_fin_name_results, 3
            ),
            parse_key_statistic_results_item_value(
                cash_flow_statement_fin_name_results, 4
            ),
        )
        fundamental.cash_flow_statement = cash_flow_statement
        logger.debug(cash_flow_statement)

        # Price Performance
        #
        price_performance_fin_name_results = closure_fin_items_results[11][
            "fin_name_results"
        ]
        price_performance = PricePerformance(
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 0
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 1
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 2
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 3
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 4
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 5
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 6
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 7
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 8
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 9
            ),
            parse_key_statistic_results_item_value(
                price_performance_fin_name_results, 10
            ),
        )
        fundamental.price_performance = price_performance

        return fundamental

    def stock_price_by_stock(self, stock: Stock) -> Stock:
        """
        Fetches the stock price data for a given stock.

        This method constructs a URL using the base URL and the stock's ticker symbol,
        then makes an HTTP GET request to retrieve the stock price data associated with that stock.

        Parameters:
        - stock (Stock): An instance of the Stock class containing the ticker symbol
          for which the stock price data is to be fetched.

        Returns:
        - Stock: The stock price data extracted from the response.
        """
        url = (
            f"{self.base_url}/company-price-feed/v2/orderbook/companies/{stock.ticker}"
        )

        return self.stockbit_api_client.get(url)

    def with_stock_price(self):
        """
        Updates each stock in the stocks list with detailed price data concurrently.
        """
        settings = get_settings()
        max_workers = getattr(settings, "STOCKBIT_MAX_WORKERS", 10)

        processed_count = 0
        processed_lock = threading.Lock()

        def safe_fetch(stock):
            nonlocal processed_count
            try:
                response = self._safe_fetch_stock_price(stock)

                if response == {}:
                    logger.warning(
                        f"Skipped to fetch stock price for {stock.ticker} because empty response!"
                    )
                    return

                data = response.get("data")
                if not data:
                    return

                stock.stock_price = StockPrice(
                    price=data["lastprice"],
                    change=data["change"],
                    fbuy=data["fbuy"],
                    fsell=data["fsell"],
                    volume=data["volume"],
                    percentage_change=data["percentage_change"],
                    average=data["average"],
                    close=data["close"],
                    high=data["high"],
                    low=data["low"],
                    open=data["open"],
                    ara=float(data["ara"]["value"].replace(",", "")),
                    arb=float(data["arb"]["value"].replace(",", "")),
                    frequency=data["frequency"],
                )

                with processed_lock:
                    processed_count += 1
                    logger.info(
                        f"Processing stock price for: {stock.ticker} ({processed_count}/{len(self.stocks)})"
                    )
            except Exception as e:
                logger.warning(f"Failed stock price for {stock.ticker}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(safe_fetch, self.stocks))

        return self

    def _safe_fetch_key_statistic(self, stock: Stock) -> dict:
        """
        Fetches key statistics and retries once after re-authentication when 401 is detected.
        """
        try:
            return self.key_statistic_by_stock(stock)
        except Exception as e:
            if "401" in str(e):
                logger.warning(
                    f"Token expired while fetching key statistics for {stock.ticker}, re-authenticating."
                )
                self.stockbit_api_client.reauthenticate()
                return self.key_statistic_by_stock(stock)
            raise

    def _safe_fetch_stock_price(self, stock: Stock) -> dict:
        """
        Fetches stock price and retries once after re-authentication when 401 is detected.
        """
        try:
            return self.stock_price_by_stock(stock)
        except Exception as e:
            if "401" in str(e):
                logger.warning(
                    f"Token expired while fetching stock price for {stock.ticker}, re-authenticating."
                )
                self.stockbit_api_client.reauthenticate()
                return self.stock_price_by_stock(stock)
            raise

    def stream_pinned_by_stock(self, stock: Stock) -> dict:
        """
        Fetches the pinned stream data for a given stock.

        This method constructs a URL using the base URL and the stock's ticker symbol,
        then makes an HTTP GET request to retrieve the pinned stream data associated
        with that stock.

        Parameters:
        - stock (Stock): An instance of the Stock class containing the ticker symbol
          for which the pinned stream data is to be fetched.

        Returns:
        - dict: A dictionary containing the response data from the HTTP GET request.
        """
        url = f"{self.base_url}/stream/v3/symbol/{stock.ticker}/pinned"

        return self.stockbit_api_client.get(url)

    @staticmethod
    def _ticker(stock: Stock | str) -> str:
        return getattr(stock, "ticker", str(stock))

    @staticmethod
    def _extract_stream_posts(raw: dict | list | None) -> list[dict]:
        if not raw:
            return []
        if isinstance(raw, list):
            return [post for post in raw if isinstance(post, dict)]
        if not isinstance(raw, dict):
            return []

        data = raw.get("data")
        if isinstance(data, list):
            return [post for post in data if isinstance(post, dict)]
        if isinstance(data, dict):
            stream = data.get("stream")
            if isinstance(stream, list):
                return [post for post in stream if isinstance(post, dict)]
            if any(key in data for key in ("stream_id", "id", "post_id", "content")):
                return [data]

        stream = raw.get("stream")
        if isinstance(stream, list):
            return [post for post in stream if isinstance(post, dict)]
        if any(key in raw for key in ("stream_id", "id", "post_id", "content")):
            return [raw]
        return []

    @staticmethod
    def _stream_post_key(post: dict) -> str | None:
        for key in ("stream_id", "id", "post_id"):
            value = post.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    @staticmethod
    def _apply_verified_weight(post: dict) -> dict:
        weighted_post = dict(post)
        user = weighted_post.get("user")
        is_verified = isinstance(user, dict) and user.get("is_verified") is True
        weighted_post["_verified_weight"] = 1.5 if is_verified else 1.0
        return weighted_post

    @classmethod
    def _merge_stream_posts(cls, ticker: str, *post_groups: list[dict]) -> list[dict]:
        seen_keys: set[str] = set()
        combined: list[dict] = []
        for post in [post for group in post_groups for post in group]:
            post_key = cls._stream_post_key(post)
            if post_key is None:
                logger.warning(
                    f"Stockbit stream merge for {ticker}: post missing "
                    "stream_id/id/post_id; including without dedup"
                )
                combined.append(cls._apply_verified_weight(post))
                continue
            if post_key in seen_keys:
                continue
            seen_keys.add(post_key)
            combined.append(cls._apply_verified_weight(post))
        return combined

    def stream_by_stock(
        self,
        stock: Stock | str,
        category: str = "STREAM_CATEGORY_IDEAS",
    ) -> dict:
        """
        Fetches the stream data for a given stock.

        This method constructs a URL using the base URL and the stock's ticker symbol,
        then makes an HTTP POST request to retrieve the stream data associated with that stock.
        The request includes a payload specifying the category, last stream ID, and limit.

        Parameters:
        - stock (Stock): An instance of the Stock class containing the ticker symbol
          for which the stream data is to be fetched.

        Returns:
        - dict: A dictionary containing the response data from the HTTP POST request.
        """
        ticker = self._ticker(stock)
        url = f"{self.base_url}/stream/v3/symbol/{ticker}"
        payload = {"category": category, "last_stream_id": 0, "limit": 20}
        return self.stockbit_api_client.post(url, payload)

    def with_stream_data(self):
        """
        Updates each stock in the stocks list with sentiment data from stream and pinned stream sources concurrently.
        """
        settings = get_settings()
        max_workers = getattr(settings, "STOCKBIT_MAX_WORKERS", 10)

        processed_count = 0
        processed_lock = threading.Lock()

        def safe_fetch(stock):
            nonlocal processed_count
            try:
                combined_posts = self._safe_fetch_stream_data(stock)
                stock.sentiment = [
                    Sentiment(
                        content=post.get("content", ""),
                        posted_at=datetime.fromisoformat(post["created_at"]),
                    )
                    for post in combined_posts
                ]

                with processed_lock:
                    processed_count += 1
                    logger.info(
                        f"Processing stream data for: {stock.ticker} ({processed_count}/{len(self.stocks)})"
                    )
            except Exception as e:
                logger.warning(f"Failed stream data for {stock.ticker}: {e}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(safe_fetch, self.stocks))

        return self

    def _safe_fetch_stream_data(self, stock: Stock) -> list[dict]:
        """
        Fetches pinned, IDEAS, and NEWS stream data with per-source fallbacks.
        """
        return asyncio.run(self._safe_fetch_stream_data_async(stock))

    async def _fetch_stream_source(
        self, stock: Stock, operation: str, category: str | None = None
    ) -> dict:
        ticker = self._ticker(stock)
        try:
            if operation == "pinned":
                return await asyncio.to_thread(self.stream_pinned_by_stock, stock)
            return await asyncio.to_thread(
                self.stream_by_stock, stock, category=category
            )
        except Exception as exc:
            if "401" in str(exc):
                logger.warning(
                    f"Token expired while fetching Stockbit {operation} "
                    f"for {ticker}, re-authenticating: {exc}"
                )
                try:
                    await asyncio.to_thread(self.stockbit_api_client.reauthenticate)
                    if operation == "pinned":
                        return await asyncio.to_thread(
                            self.stream_pinned_by_stock, stock
                        )
                    return await asyncio.to_thread(
                        self.stream_by_stock,
                        stock,
                        category=category,
                    )
                except Exception as retry_exc:
                    logger.warning(
                        f"Stockbit {operation} retry failed for {ticker}: {retry_exc}"
                    )
                    return {}
            logger.warning(f"Stockbit {operation} fetch failed for {ticker}: {exc}")
            return {}

    async def _safe_fetch_stream_data_async(self, stock: Stock) -> list[dict]:
        ticker = self._ticker(stock)
        pinned_raw, ideas_raw, news_raw = await asyncio.gather(
            self._fetch_stream_source(stock, "pinned"),
            self._fetch_stream_source(
                stock,
                "STREAM_CATEGORY_IDEAS",
                category="STREAM_CATEGORY_IDEAS",
            ),
            self._fetch_stream_source(
                stock,
                "STREAM_CATEGORY_NEWS",
                category="STREAM_CATEGORY_NEWS",
            ),
        )
        pinned_posts = self._extract_stream_posts(pinned_raw)
        ideas_posts = self._extract_stream_posts(ideas_raw)
        news_posts = self._extract_stream_posts(news_raw)
        total_before_dedup = len(pinned_posts) + len(ideas_posts) + len(news_posts)
        combined_posts = self._merge_stream_posts(
            ticker,
            pinned_posts,
            ideas_posts,
            news_posts,
        )
        verified_count = sum(
            1 for post in combined_posts if post.get("_verified_weight") == 1.5
        )
        logger.debug(
            f"Stockbit stream data for {ticker}: "
            f"pinned={len(pinned_posts)} ideas={len(ideas_posts)} "
            f"news={len(news_posts)} total_before_dedup={total_before_dedup} "
            f"total_after_dedup={len(combined_posts)} "
            f"verified_posts={verified_count}"
        )
        return combined_posts
