"""
core/dependency_validator.py — Staleness check untuk input pipeline.

Mengecek umur top10_candidates.json dan menawarkan dua opsi saat stale:
  - Raise informative error (default, CANDIDATES_AUTO_RERUN=False)
  - Auto-rerun run_quant_filter.py via subprocess (CANDIDATES_AUTO_RERUN=True)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core.settings import settings
from db import database, db_path
from utils.logger_config import logger


@dataclass
class ValidationResult:
    """Hasil validasi staleness file kandidat."""

    is_valid: bool
    age_hours: float
    message: str


@dataclass
class DependencyCheck:
    """Single pre-flight dependency check result."""

    name: str
    is_valid: bool
    message: str
    hint: str = ""
    blocking: bool = True


@dataclass
class DependencyCheckResult:
    """Aggregate pre-flight dependency status for the orchestrator."""

    is_valid: bool
    checks: dict[str, DependencyCheck]
    failed_checks: list[str]
    blocking_issues: list[str]


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


def check_llm_api_key(required: bool = True) -> DependencyCheck:
    """Verify that the API key for the active provider is available."""
    provider = settings.DEFAULT_LLM_PROVIDER.lower()

    if provider == "gemini":
        api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        key_name = "GEMINI_API_KEY"
    elif provider == "anthropic":
        api_key = settings.ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        key_name = "ANTHROPIC_API_KEY"
    elif provider == "codex":
        if not required:
            return DependencyCheck(
                name="llm_api_key",
                is_valid=True,
                message="Otentikasi Codex dilewati untuk dry-run.",
                blocking=False,
            )
        try:
            from providers.oauth_manager import resolve_codex_token

            token = resolve_codex_token()
        except Exception as exc:
            return DependencyCheck(
                name="llm_api_key",
                is_valid=False,
                message=f"Token Codex tidak valid atau tidak tersedia: {exc}",
                hint="Jalankan `idx auth codex` atau refresh login Codex CLI.",
                blocking=required,
            )
        if not str(token or "").strip():
            return DependencyCheck(
                name="llm_api_key",
                is_valid=False,
                message="Token Codex kosong.",
                hint="Jalankan `idx auth codex` atau refresh login Codex CLI.",
                blocking=required,
            )
        return DependencyCheck(
            name="llm_api_key",
            is_valid=True,
            message="Token Codex tersedia di auth store.",
            blocking=False,
        )
    else:
        key_name = f"{provider.upper()}_API_KEY"
        api_key = ""

    if api_key.strip():
        return DependencyCheck(
            name="llm_api_key",
            is_valid=True,
            message=f"{key_name} tersedia untuk provider {provider}.",
            blocking=False,
        )

    return DependencyCheck(
        name="llm_api_key",
        is_valid=not required,
        message=f"{key_name} kosong.",
        hint=f"Isi {key_name} di .env untuk menjalankan debate real.",
        blocking=required,
    )


def _invoke_llm_probe(provider: str, tier: str) -> None:
    """Run a tiny live model call for providers that need real access proof."""
    from providers.llm_factory import get_llm

    model = get_llm("flash" if tier == "flash" else "pro", provider=provider)
    response = model.invoke("Reply with OK only.")
    content = getattr(response, "content", response)
    if content is None or not str(content).strip():
        raise RuntimeError(f"{provider} {tier} probe returned an empty response")


def check_database_connection() -> DependencyCheck:
    """Open a lightweight SQLAlchemy connection and run SELECT 1."""
    db_file = Path(db_path)
    if settings.DATABASE_TYPE == "sqlite" and not db_file.exists():
        return DependencyCheck(
            name="database",
            is_valid=False,
            message=f"Database SQLite belum ada: {db_file}",
            hint="Jalankan main.py atau orchestrator.py setelah data siap untuk bootstrap DB.",
            blocking=True,
        )

    try:
        with database.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return DependencyCheck(
            name="database",
            is_valid=True,
            message="Koneksi database berhasil.",
            blocking=False,
        )
    except Exception as exc:
        return DependencyCheck(
            name="database",
            is_valid=False,
            message=f"Koneksi database gagal: {exc}",
            hint="Periksa konfigurasi database dan file db/idx-fundamental.db.",
            blocking=True,
        )


def check_disk_space(path: Path, required_gb: float = 5.0) -> DependencyCheck:
    """Ensure enough free disk space exists for batch output and logs."""
    target = path if path.exists() else path.parent
    target.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(target).free / (1024**3)
    if free_gb >= required_gb:
        return DependencyCheck(
            name="disk_space",
            is_valid=True,
            message=f"Ruang kosong {free_gb:.1f} GB.",
            blocking=False,
        )
    return DependencyCheck(
        name="disk_space",
        is_valid=False,
        message=f"Ruang kosong hanya {free_gb:.1f} GB (butuh {required_gb:.1f} GB).",
        hint="Kosongkan disk atau arahkan --output-dir ke drive lain.",
        blocking=True,
    )


def check_llm_models(required: bool = True) -> DependencyCheck:
    """Validate configured model names are present for the active provider."""
    provider = settings.DEFAULT_LLM_PROVIDER.lower()

    if provider == "gemini":
        flash = settings.GEMINI_FLASH_MODEL
        pro = settings.GEMINI_PRO_MODEL
    elif provider == "anthropic":
        flash = settings.ANTHROPIC_FLASH_MODEL
        pro = settings.ANTHROPIC_PRO_MODEL
    elif provider == "codex":
        flash = settings.CODEX_FLASH_MODEL
        pro = settings.CODEX_PRO_MODEL
    else:
        flash = ""
        pro = ""

    missing = []
    if not str(flash or "").strip():
        missing.append("FLASH_MODEL")
    if not str(pro or "").strip():
        missing.append("PRO_MODEL")

    if not missing:
        if provider == "codex" and not required:
            return DependencyCheck(
                name="llm_models",
                is_valid=True,
                message=(
                    "Codex model live probe dilewati untuk dry-run: "
                    f"flash={flash}, pro={pro}."
                ),
                blocking=False,
            )
        if provider == "codex" and required:
            import concurrent.futures

            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    f1 = executor.submit(_invoke_llm_probe, provider, "flash")
                    f2 = executor.submit(_invoke_llm_probe, provider, "pro")
                    f1.result()
                    f2.result()
            except Exception as exc:
                return DependencyCheck(
                    name="llm_models",
                    is_valid=False,
                    message=f"Codex model live probe gagal: {exc}",
                    hint=(
                        "Periksa DEFAULT_LLM_PROVIDER, CODEX_FLASH_MODEL, "
                        "CODEX_PRO_MODEL, dan token Codex."
                    ),
                    blocking=required,
                )
            return DependencyCheck(
                name="llm_models",
                is_valid=True,
                message=(f"Model {provider} live probe OK: flash={flash}, pro={pro}."),
                blocking=False,
            )
        return DependencyCheck(
            name="llm_models",
            is_valid=True,
            message=f"Model {provider} configured: flash={flash}, pro={pro}.",
            blocking=False,
        )
    return DependencyCheck(
        name="llm_models",
        is_valid=not required,
        message=f"Model {provider} belum lengkap: {', '.join(missing)}.",
        hint=f"Gunakan `idx model` untuk mengonfigurasi model {provider}.",
        blocking=required,
    )


def check_all_dependencies(
    output_dir: Path,
    *,
    require_llm: bool = True,
    required_disk_gb: float = 5.0,
) -> DependencyCheckResult:
    """Run orchestrator pre-flight checks and return an aggregate report."""
    checks = {
        "llm_api_key": check_llm_api_key(required=require_llm),
        "database": check_database_connection(),
        "disk_space": check_disk_space(output_dir, required_gb=required_disk_gb),
        "llm_models": check_llm_models(required=require_llm),
    }
    failed = [name for name, result in checks.items() if not result.is_valid]
    blocking = [
        result.message
        for result in checks.values()
        if not result.is_valid and result.blocking
    ]
    return DependencyCheckResult(
        is_valid=not blocking,
        checks=checks,
        failed_checks=failed,
        blocking_issues=blocking,
    )


def maybe_rerun_quant_filter(
    script_path: str = "run_quant_filter.py",
    output_dir: Path | str | None = None,
    mode: str = "momentum",
) -> bool:
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

    command = [sys.executable, str(script)]
    if output_dir is not None:
        command.extend(["--output-dir", str(output_dir)])
    norm_mode = (
        "mean_reversion"
        if str(mode).replace("-", "_") == "mean_reversion"
        else "momentum"
    )
    command.extend(["--mode", norm_mode])

    logger.info("[Validator] Auto-rerun: menjalankan " + " ".join(command[1:]) + " ...")
    result = subprocess.run(
        command,
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
