import os
import re

file_path = "providers/stockbit.py"
content = open(file_path, "r", encoding="utf-8").read()

# 1. Add concurrent.futures and threading to imports
if "import concurrent.futures" not in content:
    content = content.replace("import time", "import time\nimport concurrent.futures\nimport threading")

# 2. Add setting import
if "from core.settings import get_settings" not in content:
    content = content.replace("from dotenv import load_dotenv", "from dotenv import load_dotenv\nfrom core.settings import get_settings")

# 3. Replace with_fundamental
old_with_fundamental = '''    def with_fundamental(self):
        """
        Get fundamentals for a list of stocks.

        Returns:
            Self
        """
        processed = 1
        for stock in self.stocks:
            logger.info(
                f"Processing key statistic for: {stock.ticker} ({processed}/{len(self.stocks)})"
            )
            self.key_statistic = self._safe_fetch_key_statistic(stock)

            if self.key_statistic:
                stock.fundamental = self._fundamental(stock)

            time.sleep(0.1)
            logger.debug(stock)
            processed += 1

        return self'''

new_with_fundamental = '''    def with_fundamental(self):
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

        return self'''

content = content.replace(old_with_fundamental, new_with_fundamental)

# 4. Refactor _fundamental
old_fundamental_def = '''    def _fundamental(self, stock: Stock) -> Fundamental | None:'''
new_fundamental_def = '''    def _fundamental(self, stock: Stock, key_statistic: dict) -> Fundamental | None:'''
content = content.replace(old_fundamental_def, new_fundamental_def)

old_fundamental_check = '''        if self.key_statistic == {}:
            return None

        fundamental = Fundamental()
        fundamental.stock = stock

        data = self.key_statistic["data"]'''

new_fundamental_check = '''        if not key_statistic:
            return None

        fundamental = Fundamental()
        fundamental.stock = stock

        data = key_statistic.get("data")
        if not data:
            return None'''
content = content.replace(old_fundamental_check, new_fundamental_check)

# 5. Replace with_stock_price
old_with_stock_price = '''    def with_stock_price(self):
        """
        Updates each stock in the stocks list with detailed price data.

        This method iterates over each stock in the `stocks` list, fetching the latest stock price data.
        It updates various attributes of the stock with the retrieved data, such as last price, change, volume, etc.
        The method pauses briefly between processing each stock to avoid overwhelming the server with requests.

        Returns:
        - self: The instance of the class, allowing for method chaining.
        """
        processed = 1
        for stock in self.stocks:
            logger.info(
                f"Processing stock price for: {stock.ticker} ({processed}/{len(self.stocks)})"
            )
            response = self._safe_fetch_stock_price(stock)

            if response == {}:
                logger.warning(
                    f"Skipped to fetch stock price for {stock.ticker} because empty response!"
                )
                continue

            data = response["data"]

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

            time.sleep(0.1)

            logger.debug(stock)
            processed += 1

        return self'''

new_with_stock_price = '''    def with_stock_price(self):
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

        return self'''
content = content.replace(old_with_stock_price, new_with_stock_price)

# 6. Replace with_stream_data
old_with_stream_data = '''    def with_stream_data(self):
        """
        Updates each stock in the stocks list with sentiment data from stream and pinned stream sources.

        This method iterates over each stock in the `stocks` list, fetching both pinned and regular stream data.
        It processes the response to extract sentiment information, which is then added to the stock's sentiment attribute.
        The method pauses briefly between processing each stock to avoid overwhelming the server with requests.

        Returns:
        - self: The instance of the class, allowing for method chaining.
        """
        processed = 1
        for stock in self.stocks:
            logger.info(
                f"Processing stream data for: {stock.ticker} ({processed}/{len(self.stocks)})"
            )
            response_stream_pinned, response_stream = self._safe_fetch_stream_data(stock)

            if response_stream_pinned != {}:
                pinned_data = response_stream_pinned["data"]

                if pinned_data is not None:
                    posted_at = datetime.fromisoformat(pinned_data["created_at"])
                    sentiment = Sentiment(
                        content=pinned_data["content"], posted_at=posted_at
                    )

                    stock.sentiment = [sentiment]

            if response_stream != {}:
                stream_data = response_stream["data"]["stream"]

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

            time.sleep(0.1)
            processed += 1
            logger.debug(stock)

        return self'''

new_with_stream_data = '''    def with_stream_data(self):
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

        return self'''
content = content.replace(old_with_stream_data, new_with_stream_data)

open(file_path, "w", encoding="utf-8").write(content)
print("Patching providers/stockbit.py completed.")
