import json
import os

from dotenv import load_dotenv

from builders.analysers.fundamental_analyser import FundamentalAnalyser
from builders.analysers.key_analysis_analyser import KeyAnalysisAnalyser
from builders.analysers.sentiment_analyser import SentimentAnalyser
from builders.analysers.stock_price_analyser import StockPriceAnalyser
from builders.builder_interface import BuilderInterface
from services.google_drive_service import GoogleDriveService
from utils.logger_config import logger

load_dotenv()


class Spreadsheet(BuilderInterface):
    """
    A class to manage the creation and data insertion of a Google Spreadsheet.

    Attributes:
        title (str): The title of the spreadsheet.
        google_drive_service (GoogleDriveService): An instance of GoogleDriveService to interact with Google Drive.
        spreadsheet_id (str): The ID of the created spreadsheet.
    """

    def __init__(
        self,
        title: str,
        fundamental_analyser: FundamentalAnalyser,
        sentiment_analyser: SentimentAnalyser,
        key_analysis_analyser: KeyAnalysisAnalyser,
        stock_price_analyser: StockPriceAnalyser,
    ):
        """
        Initializes the Spreadsheet class with a title and creates a new spreadsheet.

        Args:
            title (str): The title of the spreadsheet.
        """
        self.title = title
        self.fundamental_analyser = fundamental_analyser
        self.google_drive_service = GoogleDriveService()
        self.spreadsheet_id = ""
        self.sentiment_analyser = sentiment_analyser
        self.key_analysis_analyser = key_analysis_analyser
        self.stock_price_analyser = stock_price_analyser
        self._create()

    def _create(self):
        """
        Creates a new spreadsheet and sets permissions for specified Google Drive emails.
        """
        self.spreadsheet_id = self.google_drive_service.create_spreadsheet(
            title=self.title
        )

        google_drive_emails = json.loads(os.getenv("GOOGLE_DRIVE_EMAILS"))
        for google_drive_email in google_drive_emails:
            self.google_drive_service.add_drive_permission(
                self.spreadsheet_id, google_drive_email
            )

    def insert_stock(self):
        """
        Inserts stock data into the spreadsheet.
        """
        self.google_drive_service.insert_data(
            self.spreadsheet_id, "idx-stocks", self.fundamental_analyser.stocks_sheet()
        )
        logger.info(
            f"Stocks has been inserted on https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
        )

    def insert_key_statistic(self):
        """
        Inserts keystatistic data into the spreadsheet.
        """
        self.google_drive_service.insert_data(
            self.spreadsheet_id,
            "key-statistics",
            self.fundamental_analyser.key_statistics_sheet(),
        )
        logger.info(
            f"Key statistics has been inserted on https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
        )

    def insert_key_analysis(self):
        """
        Inserts fundamental analysis data into the spreadsheet.
        """

        self.google_drive_service.insert_data(
            self.spreadsheet_id, "analyses", self.key_analysis_analyser.analysis_sheet()
        )

        logger.info(
            f"Analysis has been inserted on https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
        )

    def insert_sentiment(self):
        """
        Inserts sentiment analysis data into the spreadsheet.
        """

        self.google_drive_service.insert_data(
            self.spreadsheet_id,
            "sentiments",
            self.sentiment_analyser.sentiment_sheet(),
        )

        logger.info(
            f"Sentiment has been inserted on https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
        )

    def insert_stock_price(self):
        """
        Inserts stock price data into the spreadsheet.
        """

        self.google_drive_service.insert_data(
            self.spreadsheet_id,
            "stock-prices",
            self.stock_price_analyser.stock_price_sheet(),
        )

        logger.info(
            f"Sentiment has been inserted on https://docs.google.com/spreadsheets/d/{self.spreadsheet_id}"
        )
