from builders.analysers.fundamental_analyser import FundamentalAnalyser
from builders.analysers.key_analysis_analyser import KeyAnalysisAnalyser
from builders.analysers.sentiment_analyser import SentimentAnalyser
from builders.analysers.stock_price_analyser import StockPriceAnalyser
from schemas.stock import Stock
from schemas.builder import BuilderOutputType


class Analyser:
    def __init__(self, stocks: [Stock]):
        self.stocks = stocks
        self.fundamental_analyser = FundamentalAnalyser(stocks=stocks)
        self.sentiment_analyser = SentimentAnalyser(stocks=stocks)
        self.key_analysis_analyser = KeyAnalysisAnalyser(stocks=stocks)
        self.stock_price_analyser = StockPriceAnalyser(stocks=stocks)
        self.output: BuilderOutputType = None

    async def build(self, output: BuilderOutputType, title: str):
        self.output = output
        if output == BuilderOutputType.EXCEL:
            from builders.excel import Excel

            await self._build_output(Excel, title)
        elif output == BuilderOutputType.SPREADSHEET:
            from builders.spreadsheet import Spreadsheet

            await self._build_output(Spreadsheet, title)
        else:
            raise ValueError("Unsupported output method")

    async def _build_output(self, builder_class, title):
        builder = builder_class(
            title=title,
            fundamental_analyser=self.fundamental_analyser,
            sentiment_analyser=self.sentiment_analyser,
            key_analysis_analyser=self.key_analysis_analyser,
            stock_price_analyser=self.stock_price_analyser,
        )
        await builder.insert_key_analysis()
        await builder.insert_stock()
        await builder.insert_stock_price()
        await builder.insert_key_statistic()
        await builder.insert_sentiment()

        if self.output == BuilderOutputType.EXCEL:
            builder.save()
