"""
IDX Class Documentation
==========================

**Class Description**
--------------------

The `IDX` class is a provider for retrieving stock data from the IDX (Indonesian Stock Exchange) website.
It uses Selenium WebDriver to interact with the website and extracts relevant data from the stock list page.

**Class Methods**
----------------

### `__init__`

*   Initializes the `IDX` provider with a Chrome WebDriver instance and sets the base URL for the IDX website.
*   Logs a debug message indicating the provider has been initialized.

### `stocks`

*   Retrieves a list of stock data from the IDX website.
*   Returns a list of `Stock` objects, each containing the following attributes:
    + `ticker`: The stock ticker symbol.
    + `name`: The stock name.
    + `ipo_date`: The initial public offering date.
    + `market_cap`: The market capitalization (float).
    + `note`: The stock note.

**Notes**
------

*   The `stocks` method uses Selenium WebDriver to navigate to the IDX website, wait for the table to load,
    and extract the relevant data.
*   The method uses XPath expressions to locate the table elements and extract the data.

"""

import re

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as expect
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.ui import WebDriverWait

from schemas.stock import Stock
from utils.logger_config import logger


class IDX:
    """
    IDX Provider Class
    """

    def __init__(self, is_full_retrieve=True, is_second_page=False, driver=None):
        """
        Initializes the IDX provider with a Chrome WebDriver instance and sets the base URL for the IDX website.
        """
        logger.info("IDX provider initialised")
        self.base_url = "https://idx.co.id"
        self.is_full_retrieve = is_full_retrieve
        self.is_second_page = is_second_page

        if driver is not None:
            self.driver = driver
            self._own_driver = False
        else:
            options = webdriver.ChromeOptions()
            options.add_argument("--disable-blink-features=AutomationControlled")
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
            options.add_argument("start-maximized")
            options.add_argument(
                "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
            self.driver = webdriver.Chrome(options=options)
            self._own_driver = True

        self.wait = WebDriverWait(self.driver, 15)

    def _wait_for_table(self, url: str) -> None:
        try:
            self.wait.until(
                expect.presence_of_element_located((By.XPATH, '//*[@id="vgt-table"]'))
            )
        except TimeoutException as exc:
            page_source = self.driver.page_source.lower()
            if "cloudflare" in page_source and (
                "just a moment" in page_source or "checking your browser" in page_source
            ):
                logger.error(
                    "Blocked by Cloudflare while loading stock list page at %s", url
                )
                raise RuntimeError(
                    "Cloudflare protection blocked automated access to IDX stock list page."
                ) from exc
            raise

    def stocks(self) -> [Stock]:
        """
        Retrieves a list of stock data from the IDX website.

        Returns:
            [Stock]: list of Stock object containing parsed stock data.
        """
        url = f"{self.base_url}/id/data-pasar/data-saham/daftar-saham/"

        self.driver.get(url)

        # Wait for initial table or detect Cloudflare challenge
        self._wait_for_table(url)

        # if true it will retrieve all stocks, otherwise 10 stocks only
        if self.is_full_retrieve:
            # Wait for the rows-per-page dropdown to be present
            self.wait.until(
                expect.presence_of_element_located((By.NAME, "perPageSelect"))
            )

            # Find the dropdown
            rows_per_page_dropdown = Select(
                self.driver.find_element(By.NAME, "perPageSelect")
            )

            # Select the option to retrieve all stocks
            rows_per_page_dropdown.select_by_value("-1")

            # Wait for the full table to load after changing page size
            self._wait_for_table(url)

        if self.is_second_page:
            # go to second page
            self.wait.until(
                expect.presence_of_element_located((By.NAME, "perPageSelect"))
            )

            third_button = self.driver.find_element(
                By.CSS_SELECTOR, "button.footer__navigation__page-btn:nth-child(4)"
            )

            third_button.click()

            # wait for table after navigating
            self._wait_for_table(url)

            # go to second page
            self.wait.until(
                expect.presence_of_element_located((By.NAME, "perPageSelect"))
            )

            third_button = self.driver.find_element(
                By.CSS_SELECTOR, "button.footer__navigation__page-btn:nth-child(4)"
            )

            third_button.click()

            # wait for table after navigating
            self._wait_for_table(url)

        # Wait for the table to update, adjust the time if necessary
        self._wait_for_table(url)

        # Find the table
        table = self.driver.find_element(By.XPATH, '//*[@id="vgt-table"]')

        # Parse tables by XPATH, the way to find XPATH is by inspect element
        # This is the XPATH for first ticker: table/tbody/tr[1]/td[1]/span
        # Select all row means no index for tr tag.
        tickers = table.find_elements(By.XPATH, "./tbody/tr/td[1]/span")
        names = table.find_elements(By.XPATH, "./tbody/tr/td[2]/span")
        ipo_dates = table.find_elements(By.XPATH, "./tbody/tr/td[3]/span")
        market_caps = table.find_elements(By.XPATH, "./tbody/tr/td[4]/span")
        notes = table.find_elements(By.XPATH, "./tbody/tr/td[5]/span")

        # Append data, use array of stock schema
        stocks = []
        for index in range(len(tickers)):
            stock = Stock(
                ticker=tickers[index].text,
                name=names[index].text,
                ipo_date=ipo_dates[index].text,
                market_cap=float(re.sub(r"\D", "", market_caps[index].text)),
                note=notes[index].text,
            )
            stocks.append(stock)

        # Close browser
        if getattr(self, "_own_driver", True):
            self.driver.quit()

        logger.info(f"Stocks has been retrieved from {url}")
        return stocks
