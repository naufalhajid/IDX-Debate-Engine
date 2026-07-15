"""
core/dependency_validator.py — Staleness check untuk input pipeline.

Mengecek umur top10_candidates.json dan menawarkan dua opsi saat stale:
  - Raise informative error (default, CANDIDATES_AUTO_RERUN=False)
  - Auto-rerun run_quant_filter.py via subprocess (CANDIDATES_AUTO_RERUN=True)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from core.execution_regime import EXECUTION_REGIMES
from core.quant_filter.config import canonical_screener_mode
from core.settings import settings
from db import database, db_path
from utils.logger_config import logger
from utils.secret_redaction import redact_secrets


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


def _tail_lines(text: str, max_lines: int = 80) -> str:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    omitted = len(lines) - max_lines
    return f"... ({omitted} earlier line(s) omitted) ...\n" + "\n".join(
        lines[-max_lines:]
    )


def _summarize_quant_filter_output(output: str) -> str:
    """Condense verbose run_quant_filter.py logs into one readable status line."""
    text = str(output or "")
    patterns = [
        ("universe", r"Total ticker universe:\s*([0-9]+)"),
        ("static", r"Lolos static filter:\s*([0-9]+)\s+ticker"),
        ("yf_shape", r"Download berhasil\.\s*Shape:\s*\(([^)]+)\)"),
        ("ihsg_1m", r"IHSG return 1 bulan:\s*([+-]?[0-9.]+%)"),
        ("top", r"Top\s+([0-9]+)\s+kandidat berhasil disaring"),
        ("json", r"JSON diekspor\s*(?:->|→|.)\s*([^\r\n]+top10_candidates\.json)"),
    ]
    parts: list[str] = []
    for label, pattern in patterns:
        match = re.search(pattern, text)
        if match:
            parts.append(f"{label}={match.group(1).strip()}")

    graham_caps = len(re.findall(r"\[Graham\]", text))
    suspended = len(re.findall(r"Excluded: suspek suspended/FCA", text))
    # [Graham] caps log at WARNING level; report the remainder so the counts
    # stay disjoint instead of double-counting Graham caps as warnings.
    warnings = max(0, len(re.findall(r"\[WARNING\]", text)) - graham_caps)
    if warnings:
        parts.append(f"warnings={warnings}")
    if graham_caps:
        parts.append(f"graham_caps={graham_caps}")
    if suspended:
        parts.append(f"suspended_like={suspended}")

    return " | ".join(parts) if parts else "completed"


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


def read_candidates_screener_mode(path: Path) -> str:
    """Return the screener_mode a candidates file was produced under.

    Untagged/missing/unreadable files are treated as 'momentum' (the legacy
    default), so pre-existing momentum caches keep being reused.
    """
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(records, list) and records and isinstance(records[0], dict):
            return canonical_screener_mode(records[0].get("screener_mode"))
    except Exception:
        pass
    return "momentum"


def read_candidates_execution_regime(path: Path) -> str:
    """Return one canonical execution regime shared by every cached candidate.

    Missing, malformed, untagged, or internally mixed artifacts return
    ``UNKNOWN``. Callers can therefore fail closed and regenerate candidates
    instead of silently reusing a cache produced under another risk policy.
    """
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(records, list) or not records:
            return "UNKNOWN"
        if any(not isinstance(record, dict) for record in records):
            return "UNKNOWN"
        labels = {
            str(record.get("execution_regime") or "").strip().upper()
            for record in records
        }
        if len(labels) == 1:
            label = labels.pop()
            if label in EXECUTION_REGIMES:
                return label
    except (OSError, UnicodeError, json.JSONDecodeError):
        pass
    return "UNKNOWN"


def check_llm_api_key(required: bool = True) -> DependencyCheck:
    """Verify that the API key for the active provider is available."""
    provider = settings.DEFAULT_LLM_PROVIDER.lower()

    if provider == "gemini":
        api_key = settings.GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
        key_name = "GEMINI_API_KEY"
    elif provider == "anthropic":
        key_name = "ANTHROPIC_API_KEY"
        if not required:
            api_key = settings.ANTHROPIC_API_KEY or os.environ.get(
                "ANTHROPIC_API_KEY", ""
            )
        else:
            try:
                from providers.oauth_manager import resolve_anthropic_token

                api_key = resolve_anthropic_token()
            except Exception as exc:
                return DependencyCheck(
                    name="llm_api_key",
                    is_valid=False,
                    message=(
                        "Kredensial Anthropic tidak valid atau tidak tersedia: "
                        f"{redact_secrets(exc)}"
                    ),
                    hint=(
                        "Isi ANTHROPIC_API_KEY/CLAUDE_CODE_OAUTH_TOKEN atau "
                        "jalankan `idx auth add anthropic`."
                    ),
                    blocking=required,
                )
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
                message=(
                    "Token Codex tidak valid atau tidak tersedia: "
                    f"{redact_secrets(exc)}"
                ),
                hint="Jalankan `idx auth add codex` atau refresh login Codex CLI.",
                blocking=required,
            )
        if not str(token or "").strip():
            return DependencyCheck(
                name="llm_api_key",
                is_valid=False,
                message="Token Codex kosong.",
                hint="Jalankan `idx auth add codex` atau refresh login Codex CLI.",
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
        credential_label = (
            "Kredensial Anthropic" if provider == "anthropic" else key_name
        )
        return DependencyCheck(
            name="llm_api_key",
            is_valid=True,
            message=f"{credential_label} tersedia untuk provider {provider}.",
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
    from providers.oauth_manager import codex_token_fingerprint

    model = get_llm("flash" if tier == "flash" else "pro", provider=provider)
    try:
        response = model.invoke("Reply with OK only.")
    except Exception as exc:
        api_key = getattr(model, "api_key", None)
        token = (
            api_key.get_secret_value()
            if hasattr(api_key, "get_secret_value")
            else str(api_key or "")
        )
        fingerprint = codex_token_fingerprint(token)
        if fingerprint:
            try:
                setattr(exc, "codex_token_fingerprint", fingerprint)
            except (AttributeError, TypeError):
                wrapped = RuntimeError("Codex live probe failed")
                wrapped.codex_token_fingerprint = fingerprint
                raise wrapped from exc
        raise
    content = getattr(response, "content", response)
    if content is None or not str(content).strip():
        raise RuntimeError(f"{provider} {tier} probe returned an empty response")


def _run_codex_probe_round(provider: str) -> None:
    """Run Flash and Pro once, collecting both outcomes before returning."""
    import concurrent.futures

    errors: list[Exception] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_invoke_llm_probe, provider, "flash"),
            executor.submit(_invoke_llm_probe, provider, "pro"),
        ]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                errors.append(exc)
    if errors:
        from providers.oauth_manager import is_codex_auth_expiry_error

        auth_error = next(
            (error for error in errors if is_codex_auth_expiry_error(error)),
            None,
        )
        raise auth_error or errors[0]


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
            try:
                _run_codex_probe_round(provider)
            except Exception as exc:
                from providers.oauth_manager import (
                    CodexAuthRecoveryExhausted,
                    is_codex_auth_expiry_error,
                    recover_codex_token_after_auth_failure,
                )

                if (
                    not isinstance(exc, CodexAuthRecoveryExhausted)
                    and is_codex_auth_expiry_error(exc)
                ):
                    try:
                        recover_codex_token_after_auth_failure(
                            rejected_token_fingerprint=getattr(
                                exc,
                                "codex_token_fingerprint",
                                None,
                            )
                        )
                        _run_codex_probe_round(provider)
                    except Exception as retry_exc:
                        exc = retry_exc
                    else:
                        return DependencyCheck(
                            name="llm_models",
                            is_valid=True,
                            message=(
                                f"Model {provider} live probe OK setelah satu "
                                f"credential recovery: flash={flash}, pro={pro}."
                            ),
                            blocking=False,
                        )
                return DependencyCheck(
                    name="llm_models",
                    is_valid=False,
                    message=(
                        "Codex model live probe gagal: "
                        f"{redact_secrets(exc)}"
                    ),
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
    llm_api_key = check_llm_api_key(required=require_llm)
    if require_llm and not llm_api_key.is_valid:
        llm_models = DependencyCheck(
            name="llm_models",
            is_valid=False,
            message=(
                "Live model probe dilewati karena credential preflight gagal."
            ),
            hint=llm_api_key.hint,
            blocking=False,
        )
    else:
        llm_models = check_llm_models(required=require_llm)
    checks = {
        "llm_api_key": llm_api_key,
        "database": check_database_connection(),
        "disk_space": check_disk_space(output_dir, required_gb=required_disk_gb),
        "llm_models": llm_models,
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
    execution_regime: str | None = None,
    execution_regime_reason: str | None = None,
    trend_regime: str | None = None,
    volatility_regime: str | None = None,
) -> bool:
    """
    Jalankan run_quant_filter.py via subprocess jika CANDIDATES_AUTO_RERUN=True.

    Catatan desain: subprocess dijalankan dengan interpreter yang sama (sys.executable)
    untuk memastikan virtualenv aktif terkena. Output di-capture lalu diringkas
    menjadi satu baris status; output mentah hanya ditampilkan saat gagal.

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
    norm_mode = canonical_screener_mode(mode)
    command.extend(["--mode", norm_mode])
    if execution_regime:
        command.extend(["--execution-regime", str(execution_regime)])
    if execution_regime_reason:
        command.extend(
            ["--execution-regime-reason", str(execution_regime_reason)]
        )
    if trend_regime:
        command.extend(["--trend-regime", str(trend_regime)])
    if volatility_regime:
        command.extend(["--volatility-regime", str(volatility_regime)])

    logger.info("[Validator] Auto-rerun: menjalankan " + " ".join(command[1:]) + " ...")
    # Capture verbose quant-filter logs so the orchestrator console can show a
    # compact success summary. Raw output is surfaced only on failure.
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    captured_output = "\n".join(
        part for part in (result.stdout, result.stderr) if str(part or "").strip()
    )

    if result.returncode == 0:
        summary = _summarize_quant_filter_output(captured_output)
        logger.info(f"[Validator] Auto-rerun selesai: {summary}")
        return True

    logger.error(
        f"[Validator] Auto-rerun gagal (returncode={result.returncode}). "
        "Periksa output di atas untuk detail error."
    )
    if captured_output.strip():
        logger.error(
            "[Validator] Auto-rerun output (last lines):\n"
            + _tail_lines(captured_output)
        )
    return False
