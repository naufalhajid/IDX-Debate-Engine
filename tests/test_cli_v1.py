from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from app.cli.main import app
from app.cli.commands.pipeline import run_pipeline_cli

runner = CliRunner()


def test_root_help_lists_v1_commands():
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("scan", "filter", "debate", "pipeline", "sector"):
        assert command in result.output


def test_command_help_pages_render():
    commands = [
        ["scan", "--help"],
        ["filter", "--help"],
        ["debate", "--help"],
        ["pipeline", "--help"],
        ["sector", "--help"],
        ["sector", "build", "--help"],
        ["sector", "list", "--help"],
        ["sector", "show", "--help"],
    ]

    for command in commands:
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        assert "Usage" in result.output


def test_scan_maps_options_without_running_etl(monkeypatch):
    calls = []

    def fake_run_scan(*, full, export):
        calls.append((full, export.value))

    monkeypatch.setattr("app.cli.commands.scan.run_scan", fake_run_scan)

    result = runner.invoke(app, ["scan", "--full", "--export", "excel"])

    assert result.exit_code == 0, result.output
    assert calls == [(True, "excel")]


def test_scan_dry_run_does_not_run_etl(monkeypatch):
    calls = []

    def fake_run_scan(*, full, export):
        calls.append((full, export.value))

    monkeypatch.setattr("app.cli.commands.scan.run_scan", fake_run_scan)

    result = runner.invoke(app, ["scan", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls == []
    assert "Dry Run" in result.output


def test_filter_applies_safe_overrides(monkeypatch):
    calls = []

    def fake_run_filter(*, top, input_file, output_dir):
        calls.append((top, input_file, output_dir))

    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(
        app,
        [
            "filter",
            "--top",
            "5",
            "--input-file",
            "output/sample.xlsx",
            "--output-dir",
            "tmp/filter",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [(5, Path("output/sample.xlsx"), Path("tmp/filter"))]


def test_debate_normalizes_tickers_and_output_dir(monkeypatch):
    calls = []

    def fake_run_debate_cli(*, tickers, output_dir, verbose=False, details=True):
        calls.append((tickers, output_dir, verbose, details))

    monkeypatch.setattr("app.cli.commands.debate.run_debate_cli", fake_run_debate_cli)

    result = runner.invoke(app, ["debate", "bbri", "BBCA", "--output-dir", "tmp/debates"])

    assert result.exit_code == 0, result.output
    assert calls == [(["BBRI", "BBCA"], Path("tmp/debates"), False, True)]


def test_debate_accepts_tickers_option_for_readme_compatibility(monkeypatch):
    calls = []

    def fake_run_debate_cli(*, tickers, output_dir, verbose=False, details=True):
        calls.append((tickers, output_dir, verbose, details))

    monkeypatch.setattr("app.cli.commands.debate.run_debate_cli", fake_run_debate_cli)

    result = runner.invoke(app, ["debate", "--tickers", "bbri", "BBCA"])

    assert result.exit_code == 0, result.output
    assert calls == [(["BBRI", "BBCA"], Path("output/debates"), False, True)]


def test_debate_uses_global_verbose_flag(monkeypatch):
    calls = []

    def fake_run_debate_cli(*, tickers, output_dir, verbose=False, details=True):
        calls.append((tickers, output_dir, verbose, details))

    monkeypatch.setattr("app.cli.commands.debate.run_debate_cli", fake_run_debate_cli)

    result = runner.invoke(app, ["--verbose", "debate", "bbri"])

    assert result.exit_code == 0, result.output
    assert calls == [(["BBRI"], Path("output/debates"), True, True)]


def test_pipeline_preserves_legacy_flags(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli)

    result = runner.invoke(
        app,
        [
            "pipeline",
            "--dry-run",
            "--no-interactive",
            "--skip-scraping",
            "--output-dir",
            "tmp/idx_cli_dry_run",
            "--mode",
            "compare",
            "--verbose",
            "--tickers",
            "bbri",
            "bbca",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls == [
        {
            "dry_run": True,
            "output_dir": Path("tmp/idx_cli_dry_run"),
            "tickers": ("BBRI", "BBCA"),
            "skip_scraping": True,
            "no_interactive": True,
            "mode": "compare",
            "verbose": True,
        }
    ]


def test_pipeline_uses_global_verbose_flag(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr("app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli)

    result = runner.invoke(app, ["--verbose", "pipeline", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls[0]["verbose"] is True


def test_pipeline_runner_passes_argparse_argv_without_program_name(monkeypatch):
    calls = []

    def fake_run_cli(argv):
        calls.append(argv)

    monkeypatch.setitem(sys.modules, "orchestrator", SimpleNamespace(_run_cli=fake_run_cli))

    run_pipeline_cli(
        dry_run=True,
        output_dir=Path("tmp/out"),
        tickers=("BBRI", "BBCA"),
        skip_scraping=True,
        no_interactive=True,
        mode="multi",
        verbose=True,
    )

    assert calls == [
        [
            "--output-dir",
            str(Path("tmp/out")),
            "--mode",
            "multi",
            "--dry-run",
            "--skip-scraping",
            "--no-interactive",
            "--verbose",
            "--tickers",
            "BBRI",
            "BBCA",
        ]
    ]


def test_run_debate_cli_invokes_isolated_debate_runner(monkeypatch):
    calls = []

    async def fake_main(argv):
        calls.append(argv)

    monkeypatch.setitem(sys.modules, "run_debate", SimpleNamespace(main=fake_main))

    from app.cli.commands.debate import run_debate_cli

    run_debate_cli(
        tickers=["BBRI", "BBCA"],
        output_dir=Path("output/debates"),
        verbose=True,
        details=True,
    )

    assert calls == [
        [
            "--tickers",
            "BBRI",
            "BBCA",
            "--output-dir",
            str(Path("output/debates")),
            "--verbose",
        ]
    ]

    calls.clear()
    run_debate_cli(
        tickers=["BBRI"],
        output_dir=Path("tmp/custom"),
        verbose=False,
        details=False,
    )
    assert calls == [
        [
            "--tickers",
            "BBRI",
            "--output-dir",
            str(Path("tmp/custom")),
            "--no-details",
        ]
    ]


def test_sector_list_and_show_use_cache_file():
    cache_file = Path(".pytest-tmp-run/test_cli_v1_sector_cache.json")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache_file.write_text(
            """
            {
              "BBRI": {"sector": "bank", "yf_sector": "Financial Services", "yf_industry": "Banks"},
              "TLKM": {"sector": "tech", "yf_sector": "Communication Services", "yf_industry": "Telecom"}
            }
            """,
            encoding="utf-8",
        )

        list_result = runner.invoke(
            app,
            ["sector", "list", "--cache-file", str(cache_file)],
        )
        show_result = runner.invoke(
            app,
            ["sector", "show", "banking", "--cache-file", str(cache_file)],
        )

        assert list_result.exit_code == 0, list_result.output
        assert "bank" in list_result.output
        assert "tech" in list_result.output
        assert show_result.exit_code == 0, show_result.output
        assert "BBRI" in show_result.output
    finally:
        cache_file.unlink(missing_ok=True)
