import logging
import sys

from dotenv import load_dotenv
from loguru import logger

from core.settings import settings

load_dotenv()

level = settings.LOG_LEVEL

# Remove default Loguru sink (stderr, level=DEBUG)
logger.remove()

# Console sink with dynamic level
logger.add(
    sys.stderr,
    format=settings.LOG_FORMAT,
    level=level,
    colorize=True,  # ensure ANSI colors are used
)


# Configure the logger to write to a log file
logger.add(
    settings.LOG_APP_FILENAME,
    format=("{time:YYYY-MM-DD HH:mm:ss.SSS} {level: <8} {file}:{line} | {message}"),
    level=level,
    rotation="1 MB",  # Rotate after the log file reaches 1 MB
    retention="10 days",  # Keep rotated files for 10 days
    compression="zip",  # Compress rotated files
)

# Export the logger for use in other modules
__all__ = ["logger"]


# Function to redirect standard logging to Loguru
class InterceptHandler(logging.Handler):
    def emit(self, record):
        # Get corresponding Loguru level if it exists
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the log message
        frame, depth = logging.currentframe(), 2
        while frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )
