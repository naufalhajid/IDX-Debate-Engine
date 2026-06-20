import json
import logging
import os
import re
import subprocess
import tempfile

import undetected_chromedriver as uc
from utils.logger_config import logger

# Suppress noisy logs
for _name in (
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.remote.remote_connection",
    "urllib3",
):
    logging.getLogger(_name).setLevel(logging.WARNING)


class StockbitTokenFetcher:
    def __init__(self):
        self.login_url = "https://stockbit.com/login"
        self.sample_url = "exodus.stockbit.com/chat/v2/rooms/unread/count"

        profile_dir = os.path.join(
            os.path.expanduser("~"), ".idx-fundamental-stockbit-profile"
        )
        os.makedirs(profile_dir, exist_ok=True)

        options = uc.ChromeOptions()
        options.add_argument(f"--user-data-dir={profile_dir}")
        # options.add_argument(f"--disk-cache-dir={cache_dir}") # UC handles profile better without explicit cache dir split sometimes, but keeping user-data-dir is key.

        # Enable performance logging to capture headers
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

        # Initialize undetected-chromedriver
        # headless=False is important for manual login
        chrome_major = self._detect_chrome_major_version()
        if chrome_major is not None:
            logger.info(f"Detected Chrome major version: {chrome_major}")
            self.driver = uc.Chrome(
                options=options,
                headless=False,
                use_subprocess=True,
                version_main=chrome_major,
            )
        else:
            logger.warning(
                "Could not detect Chrome version, initializing undetected-chromedriver without version pin."
            )
            self.driver = uc.Chrome(
                options=options, headless=False, use_subprocess=True
            )

        tmp_dir = tempfile.gettempdir()
        self.token_path = os.path.join(tmp_dir, "stockbit_token.tmp")

    def _is_already_logged_in(self) -> bool:
        """Return True if the saved Chrome profile already has an active session."""
        try:
            import time
            self.driver.get("https://stockbit.com/#/")
            time.sleep(3)
            return "login" not in self.driver.current_url.lower()
        except Exception:
            return False

    def _extract_token_from_logs(self) -> str | None:
        """Scan Chrome performance logs for any exodus.stockbit.com Bearer token."""
        logs = self.driver.get_log("performance")
        access_token = None
        for entry in logs:
            try:
                message = json.loads(entry["message"])
                if message.get("message", {}).get("method") != "Network.requestWillBeSent":
                    continue
                request = message["message"]["params"].get("request", {})
                url = request.get("url", "")
                # Accept ANY authenticated request to the Stockbit API, not just sample_url.
                if "exodus.stockbit.com" not in url:
                    continue
                headers = request.get("headers", {})
                auth = headers.get("Authorization") or headers.get("authorization", "")
                if auth.startswith("Bearer "):
                    access_token = auth.split(" ", 1)[1]
                    # Keep iterating — last token wins (most recently refreshed).
            except (KeyError, json.JSONDecodeError):
                continue
        return access_token

    def fetch_tokens(self):
        import time

        driver = self.driver
        logger.info("Navigating to Stockbit login page...")
        driver.get(self.login_url)

        if self._is_already_logged_in():
            logger.info("Saved session detected — loading dashboard to trigger API calls...")
            # Navigate to main feed so the SPA fires authenticated exodus.stockbit.com
            # requests; wait long enough for Bearer token to appear in network logs.
            driver.get("https://stockbit.com/#/feed")
            time.sleep(8)
        else:
            logger.info("Please log in to Stockbit in the opened browser.")
            input("Press Enter here AFTER login succeeds and the dashboard loads... ")

        access_token = self._extract_token_from_logs()

        # Fallback: if auto-capture still failed, let the user intervene manually.
        if not access_token:
            logger.warning(
                "Token not captured automatically. "
                "Please interact with the browser (e.g. scroll the feed), then press Enter."
            )
            input("Press Enter after the Stockbit dashboard has fully loaded... ")
            access_token = self._extract_token_from_logs()

        if not access_token:
            logger.error(
                "Could not find Bearer token in captured requests. "
                "Make sure the page finished loading before pressing Enter."
            )
            return None, None

        # Capture the User-Agent used by the browser
        user_agent = driver.execute_script("return navigator.userAgent;")
        logger.info(f"User-Agent captured: {user_agent}")

        logger.info("Access token captured.")

        with open(self.token_path, "w") as f:
            f.write(access_token)

        logger.info(f"Tokens written to: {self.token_path}")

        return access_token, user_agent

    def close(self):
        try:
            self.driver.quit()
        except Exception as exc:
            logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)

    def _detect_chrome_major_version(self) -> int | None:
        """
        Detect Chrome major version to keep ChromeDriver in sync.
        """
        version_pattern = re.compile(r"(\d+)\.\d+\.\d+\.\d+")
        possible_commands = (
            [
                "reg",
                "query",
                r"HKCU\Software\Google\Chrome\BLBeacon",
                "/v",
                "version",
            ],
            [
                "reg",
                "query",
                r"HKLM\Software\Google\Chrome\BLBeacon",
                "/v",
                "version",
            ],
        )

        for command in possible_commands:
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                output = f"{result.stdout}\n{result.stderr}"
                match = version_pattern.search(output)
                if match:
                    return int(match.group(1))
            except Exception as exc:
                logger.error(f"[{__name__}] Unexpected error: {exc}", exc_info=True)
                continue

        return None
