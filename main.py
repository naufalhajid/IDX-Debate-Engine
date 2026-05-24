# ruff: noqa: E402

import argparse
import asyncio  # QW-FIX-AR2
import signal  # QW-FIX-AR2
import sys  # QW-FIX-AR2
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from builders.analysers import Analyser
from builders.database_builder import DatabaseBuilder
from db import database
from providers.idx import IDX
from providers.stockbit import StockBit
from schemas.builder import BuilderOutputType
from utils.logger_config import logger


def parse_arguments():
    parser = argparse.ArgumentParser(description="IDX Composite Fundamental Analysis")
    parser.add_argument(
        "-f",
        "--full-retrieve",
        action="store_true",
        help="Retrieve full stock data from IDX",
    )
    parser.add_argument(
        "-o",
        "--output-format",
        type=BuilderOutputType,
        choices=list(BuilderOutputType),
        default=BuilderOutputType.SPREADSHEET,
        help="Specify the output format: 'spreadsheet' for Google Spreadsheet, 'excel' for Excel file",
    )
    return parser.parse_args()


async def main_async() -> None:  # QW-FIX-AR2
    """Async entry point for ETL pipeline. # QW-FIX-AR2"""

    def _handle_shutdown(sig, frame):  # noqa: ANN001  # QW-FIX-AR2
        logger.warning(  # QW-FIX-AR2
            f"[main] Received signal {sig}. Shutting down ETL gracefully..."  # QW-FIX-AR2
        )  # QW-FIX-AR2
        sys.exit(0)  # QW-FIX-AR2

    signal.signal(signal.SIGINT, _handle_shutdown)  # QW-FIX-AR2
    signal.signal(signal.SIGTERM, _handle_shutdown)  # QW-FIX-AR2

    logger.info("IDX Composite Fundamental Analysis")
    start_time = time.time()

    args = parse_arguments()

    # Setup database
    await asyncio.to_thread(database.setup_db, is_drop_table=False)  # QW-FIX-AR2

    # Retrieve stocks from IDX
    idx = await asyncio.to_thread(  # QW-FIX-AR2
        IDX, is_full_retrieve=args.full_retrieve  # QW-FIX-AR2
    )  # QW-FIX-AR2
    stocks = await asyncio.to_thread(idx.stocks)  # QW-FIX-AR2
    logger.debug("Stocks: {}".format(stocks))
    logger.info("Total Stocks: {}".format(len(stocks)))

    # Process stocks key statistics, price, fundamental, and stream data (news) from Stockbit
    def _run_stockbit_pipeline() -> None:  # QW-FIX-AR2
        StockBit(  # QW-FIX-AR2
            stocks=stocks  # QW-FIX-AR2
        ).with_stock_price().with_fundamental().with_stream_data()  # QW-FIX-AR2

    await asyncio.to_thread(_run_stockbit_pipeline)  # QW-FIX-AR2

    # Analyser to build the output
    title = f"IDX Fundamental Analysis {date.today().strftime('%Y-%m-%d')}"
    await Analyser(stocks=stocks).build(
        output=args.output_format,
        title=title,
    )

    # Populate to database
    database_builder = DatabaseBuilder(stocks=stocks)
    await database_builder.update_or_insert_stock()
    await database_builder.insert_key_statistic()
    await database_builder.insert_key_analysis()
    await database_builder.insert_stock_price()
    await database_builder.insert_sentiment()

    elapsed = time.time() - start_time
    elapsed_minutes = elapsed / 60
    logger.info(f"Elapsed time: {elapsed_minutes:.2f} minutes")


def main() -> None:  # QW-FIX-AR2
    """Sync wrapper - preserves CLI compatibility. # QW-FIX-AR2"""
    asyncio.run(main_async())  # QW-FIX-AR2


if __name__ == "__main__":
    main()
