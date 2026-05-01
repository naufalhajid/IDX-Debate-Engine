"""
core/dependency_validator.py — Staleness check untuk input pipeline.

Mengecek umur top10_candidates.json dan menawarkan dua opsi saat stale:
  - Raise informative error (default, CANDIDATES_AUTO_RERUN=False)
  - Auto-rerun run_quant_filter.py via subprocess (CANDIDATES_AUTO_RERUN=True)
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from utils.logger_config import logger


@dataclass
class ValidationResult:
    """Hasil validasi staleness file kandidat."""
    is_valid: bool
    age_hours: float
    message: str


def check_candidates_file(path: Path, max_age_hours: float) -> ValidationResult:
    """
    Cek apakah file kandidat ada dan tidak stale.

    Args:
        path: Path ke top10_candidates.json.
        max_age_hours: Batas umur file dalam jam.

    Returns:
        ValidationResult — is_valid=False jika file hilang atau stale.
    """
    if not path.exists():
        return ValidationResult(
            is_valid=False,
            age_hours=float("inf"),
            message=(
                f"File tidak ditemukan: {path}. "
                "Jalankan run_quant_filter.py terlebih dahulu."
            ),
        )

    mtime = path.stat().st_mtime
    now = datetime.now(timezone.utc).timestamp()
    age_hours = (now - mtime) / 3600.0

    if age_hours > max_age_hours:
        return ValidationResult(
            is_valid=False,
            age_hours=round(age_hours, 2),
            message=(
                f"File stale: {path.name} berumur {age_hours:.1f} jam "
                f"(max={max_age_hours:.1f}h). "
                "Jalankan run_quant_filter.py atau set CANDIDATES_AUTO_RERUN=true."
            ),
        )

    return ValidationResult(
        is_valid=True,
        age_hours=round(age_hours, 2),
        message=f"File valid: {path.name} berumur {age_hours:.1f} jam.",
    )


def maybe_rerun_quant_filter(script_path: str = "run_quant_filter.py") -> bool:
    """
    Jalankan run_quant_filter.py via subprocess jika CANDIDATES_AUTO_RERUN=True.

    Catatan desain: subprocess dijalankan dengan interpreter yang sama (sys.executable)
    untuk memastikan virtualenv aktif terkena. stdout/stderr diteruskan langsung ke
    terminal agar progress scraping terlihat — tidak di-capture ke variabel.

    Returns:
        True jika script selesai dengan returncode 0, False jika gagal.
    """
    script = Path(script_path)
    if not script.exists():
        logger.error(f"[Validator] Script tidak ditemukan: {script_path}")
        return False

    logger.info(f"[Validator] Auto-rerun: menjalankan {script_path} ...")
    result = subprocess.run(
        [sys.executable, str(script)],
        # Tidak capture output — biarkan mengalir ke terminal
        # supaya user bisa melihat progress scraping secara real-time.
    )

    if result.returncode == 0:
        logger.info("[Validator] Auto-rerun selesai.")
        return True

    logger.error(
        f"[Validator] Auto-rerun gagal (returncode={result.returncode}). "
        "Periksa output di atas untuk detail error."
    )
    return False
