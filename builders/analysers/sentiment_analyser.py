from schemas.stock import Stock


class SentimentAnalyser:
    """
    Analyzes and organizes sentiment data for a list of stocks.

    Attributes:
    - stocks (list of Stock): A list of Stock objects to be analyzed.
    """

    def __init__(self, stocks: [Stock]):
        """
        Initializes the SentimentAnalyser with a list of stocks.

        Parameters:
        - stocks (list of Stock): A list of Stock objects containing sentiment data.
        """
        self.stocks = stocks

    def sentiment_sheet(self):
        """
        Generates a sheet of sentiment data for each stock.

        This method creates a list of lists, where each inner list represents a row
        containing sentiment information for a stock. The first row is a header row.

        Returns:
        - list of list: A list of rows, each containing sentiment data for a stock.
        """
        header = ["Ticker", "Content", "Rate", "Category", "Posted At"]

        sheet_values = [header]

        for stock in self.stocks:
            for sentiment in stock.sentiment:
                posted_at = (
                    sentiment.posted_at.isoformat()
                    if sentiment.posted_at is not None
                    else None
                )

                row = [
                    stock.ticker,
                    sentiment.content,
                    sentiment.rate,
                    sentiment.category,
                    posted_at,
                ]

                sheet_values.append(row)

        return sheet_values
