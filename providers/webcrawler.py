import asyncio

from crawl4ai import AsyncWebCrawler

from schemas.sentiment import Sentiment
from schemas.stock import Stock


class WebCrawler:
    def __init__(self, stocks: [Stock], strategy: str = "basic"):
        self.stocks = stocks
        self.strategy = strategy

    def process(self):
        asyncio.run(self.crawl())

    async def crawl(self):
        for stock in self.stocks:
            async with AsyncWebCrawler(verbose=True) as crawler:
                result = await crawler.arun(url=stock.home_page)
                print(result.markdown[:500])  # Print first 500 characters

                if stock.sentiment is None:
                    stock.sentiment = [
                        Sentiment(url=stock.home_page, content=result.markdown)
                    ]

                print(stock)


if __name__ == "__main__":
    stocks = [Stock(ticker="AAPL", home_page="https://satrya.zeroinside.id")]
    crawler = WebCrawler(stocks)
    crawler.process()
