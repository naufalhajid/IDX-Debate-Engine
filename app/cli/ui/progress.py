from __future__ import annotations

import io
import logging
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from typing import Callable

_MILESTONES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Membaca:"), "Loading Excel data..."),
    (re.compile(r"Total ticker universe:"), "Universe loaded"),
    (re.compile(r"Lolos static filter:"), "Static filtering done"),
    (re.compile(r"yfinance download attempt"), "Fetching live prices..."),
    (re.compile(r"Download berhasil"), "Processing price data..."),
    (re.compile(r"Top \d+ kandidat"), "Ranking candidates..."),
    (re.compile(r"JSON diekspor"), "Saving results..."),
]


class ProgressConsoleHandler(logging.Handler):
    """Drop-in for StreamHandler: emits nothing to console, fires milestone callbacks."""

    def __init__(self, progress_callback: Callable[[str], None] | None = None):
        super().__init__()
        self._cb = progress_callback

    def emit(self, record: logging.LogRecord) -> None:
        if self._cb:
            msg = record.getMessage()
            for pattern, label in _MILESTONES:
                if pattern.search(msg):
                    self._cb(label)
                    break


@contextmanager
def quiet_filter_pipeline(
    scratch_dir: str,
    progress_callback: Callable[[str], None] | None = None,
):
    """
    Pre-seeds logging.getLogger('quant_filter') so pipeline.py's setup_logging()
    skips adding its StreamHandler (the source of the log flood).
    File logging is preserved via our own FileHandler.
    """
    qf = logging.getLogger("quant_filter")

    if qf.handlers:
        # 2nd+ call in this process: logger already configured — silence StreamHandlers only.
        original = list(qf.handlers)
        qf.handlers = [
            h
            for h in qf.handlers
            if not isinstance(h, logging.StreamHandler)
            or isinstance(h, logging.FileHandler)
        ]
        quiet_h = ProgressConsoleHandler(progress_callback)
        qf.addHandler(quiet_h)
        try:
            yield
        finally:
            qf.handlers = original
        return

    # First call: pre-seed so setup_logging sees handlers and skips StreamHandler.
    os.makedirs(scratch_dir, exist_ok=True)
    log_file = os.path.join(
        scratch_dir, f"scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    )
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_h = logging.FileHandler(log_file, encoding="utf-8")
    file_h.setFormatter(formatter)
    quiet_h = ProgressConsoleHandler(progress_callback)

    qf.setLevel(logging.INFO)
    qf.addHandler(file_h)
    qf.addHandler(quiet_h)
    try:
        yield
    finally:
        if quiet_h in qf.handlers:
            qf.handlers.remove(quiet_h)


@contextmanager
def suppress_stderr():
    """Redirect sys.stderr to /dev/null for the duration of the block.

    Loguru resolves sys.stderr lazily at emit time, so this silences loguru's
    stderr sink without touching its sink registry. Rich Console defaults to
    stdout and is unaffected.
    """
    old = sys.stderr
    sys.stderr = io.StringIO()
    try:
        yield
    finally:
        sys.stderr = old


__all__ = ["ProgressConsoleHandler", "quiet_filter_pipeline", "suppress_stderr"]
