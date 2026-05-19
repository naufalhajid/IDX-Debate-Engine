import time
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
from services.stockbit_api_client import StockbitApiClient
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

    def stream_by_stock(self, stock: Stock) -> dict:
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
        url = f"{self.base_url}/stream/v3/symbol/{stock.ticker}"
        payload = {"category": "STREAM_CATEGORY_ALL", "last_stream_id": 0, "limit": 20}
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
                response_stream_pinned, response_stream = self._safe_fetch_stream_data(stock)

                if response_stream_pinned != {}:
                    pinned_data = response_stream_pinned.get("data")
                    if pinned_data is not None:
                        posted_at = datetime.fromisoformat(pinned_data["created_at"])
                        sentiment = Sentiment(
                            content=pinned_data["content"], posted_at=posted_at
                        )
                        stock.sentiment = [sentiment]

                if response_stream != {}:
                    stream_outer = response_stream.get("data")
                    if stream_outer:
                        stream_data = stream_outer.get("stream")
                        if stream_data is not None:
                            for stream in stream_data:
                                posted_at = datetime.fromisoformat(stream["created_at"])
                                sentiment = Sentiment(
                                    content=stream["content"], posted_at=posted_at
                                )
                                if stock.sentiment is None:
                                    stock.sentiment = [sentiment]
                                else:
                                    stock.sentiment.append(sentiment)

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

    def _safe_fetch_stream_data(self, stock: Stock) -> tuple[dict, dict]:
        """
        Fetches stream data and retries once after re-authentication when 401 is detected.
        """
        try:
            return self.stream_pinned_by_stock(stock), self.stream_by_stock(stock)
        except Exception as e:
            if "401" in str(e):
                logger.warning(
                    f"Token expired while fetching stream for {stock.ticker}, re-authenticating."
                )
                self.stockbit_api_client.reauthenticate()
                return self.stream_pinned_by_stock(stock), self.stream_by_stock(stock)
            raise
