from schemas.stock import Stock


class StockPriceAnalyser:
    """
    Analyzes and organizes stock price for a list of stocks.

    Attributes:
    - stocks (list of Stock): A list of Stock objects to be analyzed.
    """

    def __init__(self, stocks: [Stock]):
        """
        Initializes the StockPriceAnalyser with a list of stocks.

        Parameters:
        - stocks (list of Stock): A list of Stock objects containing sentiment data.
        """
        self.stocks = stocks

    def stock_price_sheet(self):
        """
        Generates a sheet of stock price data for each stock.

        This method creates a list of lists, where each inner list represents a row
        containing sentiment information for a stock. The first row is a header row.

        Returns:
        - list of list: A list of rows, each containing sentiment data for a stock.
        """
        header = [
            "Ticker",
            "Price",
            "Volume",
            "Change",
            "Percentage Change",
            "Average",
            "Close Price",
            "High Price",
            "Open Price",
            "Low Price",
            "ARA Price",
            "ARB Price",
            "Frequency",
            "Frequency Sell",
            "Frequency Buy",
            "Created At",
        ]
        sheet_values = [header]

        for stock in self.stocks:
            row = [
                stock.ticker,
                stock.stock_price.price,
                stock.stock_price.volume,
                stock.stock_price.change,
                stock.stock_price.percentage_change,
                stock.stock_price.average,
                stock.stock_price.close,
                stock.stock_price.high,
                stock.stock_price.open,
                stock.stock_price.low,
                stock.stock_price.ara,
                stock.stock_price.arb,
                stock.stock_price.frequency,
                stock.stock_price.fsell,
                stock.stock_price.fbuy,
                stock.stock_price.created_at.strftime("%Y-%m-%d"),
            ]
            sheet_values.append(row)

        return sheet_values
