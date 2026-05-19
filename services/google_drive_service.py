import json
import os

from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build

from utils.helpers import get_sheet_range

load_dotenv()


class GoogleDriveService:
    """
    A service class to interact with Google Drive and Google Sheets APIs.

    Methods
    -------
    __init__():
        Initializes the GoogleDriveService with credentials from environment variables.

    create_spreadsheet(title="My New Spreadsheet") -> str:
        Creates a new Google Sheets spreadsheet with the given title.

    add_drive_permission(file_id: str, email: str):
        Adds write permission to a Google Drive file for a specified email address.

    insert_data(sheet_id: str, sheet_title: str, values: []):
        Inserts data into a specified sheet within a Google Sheets spreadsheet.

    add_sheet(sheet_id: str, sheet_title: str):
        Adds a new sheet to an existing Google Sheets spreadsheet.
    """

    def __init__(self):
        """
        Initializes the GoogleDriveService with credentials from environment variables.
        """
        service_account_info = json.loads(os.getenv("GOOGLE_SERVICE_ACCOUNT"))
        self.creds = service_account.Credentials.from_service_account_info(
            service_account_info
        )
        self.sheet_service = build("sheets", "v4", credentials=self.creds)
        self.drive_service = build("drive", "v3", credentials=self.creds)

    def create_spreadsheet(self, title="My New Spreadsheet") -> str:
        """
        Creates a new Google Sheets spreadsheet with the given title.

        Parameters
        ----------
        title : str, optional
            The title of the new spreadsheet (default is "My New Spreadsheet").

        Returns
        -------
        str
            The ID of the created spreadsheet.
        """
        spreadsheet = {"properties": {"title": title}}

        spreadsheet = (
            self.sheet_service.spreadsheets()
            .create(body=spreadsheet, fields="spreadsheetId")
            .execute()
        )
        return spreadsheet.get("spreadsheetId")

    def add_drive_permission(self, file_id: str, email: str):
        """
        Adds write permission to a Google Drive file for a specified email address.

        Parameters
        ----------
        file_id : str
            The ID of the Google Drive file.
        email : str
            The email address to grant write permission to.
        """
        permission = {
            "type": "user",
            "role": "writer",
            "emailAddress": email,
        }

        self.drive_service.permissions().create(
            fileId=file_id, body=permission, sendNotificationEmail=False
        ).execute()

    def insert_data(self, sheet_id: str, sheet_title: str, values: []):
        """
        Inserts data into a specified sheet within a Google Sheets spreadsheet.

        Parameters
        ----------
        sheet_id : str
            The ID of the Google Sheets spreadsheet.
        sheet_title : str
            The title of the sheet to insert data into.
        values : list
            The data to be inserted into the sheet.
        """
        sheets = (
            self.sheet_service.spreadsheets()
            .get(spreadsheetId=sheet_id)
            .execute()
            .get("sheets", [])
        )
        sheet_titles = [sheet["properties"]["title"] for sheet in sheets]

        if sheet_title not in sheet_titles:
            self.add_sheet(sheet_id, sheet_title)

        body = {"values": values}

        range_name = f"{sheet_title}!{get_sheet_range(len(values[0]), len(values))}"

        self.sheet_service.spreadsheets().values().update(
            spreadsheetId=sheet_id, range=range_name, valueInputOption="RAW", body=body
        ).execute()

        self.clean_first_sheet(sheet_titles, sheet_id)

    def add_sheet(self, sheet_id, sheet_title):
        """
        Adds a new sheet to an existing Google Sheets spreadsheet.

        Parameters
        ----------
        sheet_id : str
            The ID of the Google Sheets spreadsheet.
        sheet_title : str
            The title of the new sheet to be added.
        """
        requests = [{"addSheet": {"properties": {"title": sheet_title}}}]
        body = {"requests": requests}
        self.sheet_service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id, body=body
        ).execute()

    def clean_first_sheet(self, sheet_titles, sheet_id):
        for sheet_title in sheet_titles:
            if sheet_title == "Sheet1":
                requests = [{"deleteSheet": {"sheetId": 0}}]
                body = {"requests": requests}

                self.sheet_service.spreadsheets().batchUpdate(
                    spreadsheetId=sheet_id, body=body
                ).execute()
