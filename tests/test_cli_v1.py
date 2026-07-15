from __future__ import annotations

import re
import sys
from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

from app.cli.commands.pipeline import run_pipeline_cli
from app.cli.main import app


def _strip_ansi(text: str) -> str:
    return re.sub(r"\[[0-9;]*m", "", text)

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


def test_model_codex_reasoning_flags_write_env(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "CODEX_FLASH_MODEL=custom-flash\nCODEX_PRO_MODEL=custom-pro\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.cli.commands.model.ENV_PATH", env_path)

    result = runner.invoke(
        app,
        [
            "model",
            "codex",
            "--flash-reasoning",
            "medium",
            "--pro-reasoning",
            "xhigh",
        ],
    )

    assert result.exit_code == 0, result.output
    content = env_path.read_text(encoding="utf-8")
    assert "DEFAULT_LLM_PROVIDER=codex" in content
    assert "CODEX_FLASH_MODEL=custom-flash" in content
    assert "CODEX_PRO_MODEL=custom-pro" in content
    assert "CODEX_FLASH_REASONING_EFFORT=medium" in content
    assert "CODEX_PRO_REASONING_EFFORT=xhigh" in content


def test_model_codex_interactive_writes_reasoning(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("app.cli.commands.model.ENV_PATH", env_path)

    result = runner.invoke(app, ["model"], input="3\n1\n1\n2\n4\n")

    assert result.exit_code == 0, result.output
    content = env_path.read_text(encoding="utf-8")
    assert "DEFAULT_LLM_PROVIDER=codex" in content
    assert "CODEX_FLASH_MODEL=gpt-5.4-mini" in content
    assert "CODEX_PRO_MODEL=gpt-5.5" in content
    assert "CODEX_FLASH_REASONING_EFFORT=medium" in content
    assert "CODEX_PRO_REASONING_EFFORT=xhigh" in content


def test_model_codex_rejects_extra_high_reasoning(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr("app.cli.commands.model.ENV_PATH", env_path)

    result = runner.invoke(app, ["model", "codex", "--pro-reasoning", "extra-high"])

    assert result.exit_code != 0
    assert "Use 'xhigh' for Extra High" in _strip_ansi(result.output)


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

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        calls.append((top, input_file, output_dir, mode))

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
    assert calls == [(5, Path("output/sample.xlsx"), Path("tmp/filter"), "momentum")]


def test_filter_accepts_positional_mean_reversion_mode(monkeypatch):
    calls = []

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        calls.append((top, input_file, output_dir, mode))

    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(app, ["filter", "mr"])

    assert result.exit_code == 0, result.output
    assert calls == [(10, None, Path("output"), "mean_reversion")]


def test_filter_accepts_mode_option_mean_reversion(monkeypatch):
    calls = []

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        calls.append((top, input_file, output_dir, mode))

    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(app, ["filter", "--mode", "mean-reversion"])

    assert result.exit_code == 0, result.output
    assert calls == [(10, None, Path("output"), "mean_reversion")]


def test_filter_rejects_invalid_positional_mode(monkeypatch):
    calls = []

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        calls.append((top, input_file, output_dir, mode))

    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(app, ["filter", "salah-mode"])

    assert result.exit_code != 0
    assert calls == []
    assert "screener mode must be one of" in result.output


def test_filter_rejects_conflicting_positional_and_option_modes(monkeypatch):
    calls = []

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        calls.append((top, input_file, output_dir, mode))

    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(app, ["filter", "mr", "--mode", "momentum"])

    assert result.exit_code != 0
    assert calls == []
    assert "positional mode conflicts with --mode" in result.output


def test_filter_displays_canonical_execution_and_scoring_regimes(monkeypatch):
    import pandas as pd

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        return pd.DataFrame(
            [
                {
                    "Ticker": "BBCA",
                    "execution_regime": "DEFENSIVE",
                    "scoring_regime_profile": "DEFENSIVE",
                }
            ]
        )

    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(app, ["filter"])

    assert result.exit_code == 0, result.output
    assert "Execution regime: DEFENSIVE" in result.output
    assert "scoring profile: DEFENSIVE" in result.output


def test_filter_watchlist_displays_canonical_regime_fields(
    tmp_path: Path,
    monkeypatch,
):
    import json

    import pandas as pd

    def fake_run_filter(*, top, input_file, output_dir, mode="momentum"):
        return pd.DataFrame()

    output_dir = tmp_path / "filter"
    output_dir.mkdir()
    (output_dir / "watchlist_candidates.json").write_text(
        json.dumps(
            [
                {
                    "Ticker": "BBCA",
                    "Composite Score": 42.0,
                    "Weekly Trend": "UPTREND",
                    "execution_regime": "SIDEWAYS",
                    "scoring_regime_profile": "HIGH",
                    "score_floor": 45,
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("app.cli.commands.filter.run_filter", fake_run_filter)

    result = runner.invoke(app, ["filter", "--output-dir", str(output_dir)])

    assert result.exit_code == 0, result.output
    normalized_output = " ".join(result.output.split())
    assert "execution regime=SIDEWAYS" in normalized_output
    assert "scoring profile=HIGH" in normalized_output


def test_debate_normalizes_tickers_and_output_dir(monkeypatch):
    calls = []

    def fake_run_debate_cli(*, tickers, output_dir, verbose=False, details=True):
        calls.append((tickers, output_dir, verbose, details))

    monkeypatch.setattr("app.cli.commands.debate.run_debate_cli", fake_run_debate_cli)

    result = runner.invoke(
        app, ["debate", "bbri", "BBCA", "--output-dir", "tmp/debates"]
    )

    assert result.exit_code == 0, result.output
    assert calls == [(["BBRI", "BBCA"], Path("tmp/debates"), False, True)]


def test_debate_rejects_path_like_ticker_before_dispatch(monkeypatch):
    calls = []

    def fake_run_debate_cli(*, tickers, output_dir, verbose=False, details=True):
        calls.append((tickers, output_dir, verbose, details))

    monkeypatch.setattr("app.cli.commands.debate.run_debate_cli", fake_run_debate_cli)

    result = runner.invoke(
        app,
        ["debate", "../escape", "--output-dir", "tmp/debates"],
    )

    assert result.exit_code != 0
    assert calls == []
    assert "valid IDX ticker" in _strip_ansi(result.output)


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

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

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
            "screener_mode": "momentum",
            "verbose": True,
            "portfolio_loss_pct": None,
        }
    ]


def test_pipeline_screener_mode_threads_mean_reversion(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

    result = runner.invoke(
        app, ["pipeline", "--screener-mode", "mean-reversion", "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert calls[0]["screener_mode"] == "mean_reversion"


def test_pipeline_accepts_positional_screener_mode(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

    result = runner.invoke(app, ["pipeline", "mr", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls[0]["mode"] == "multi"
    assert calls[0]["screener_mode"] == "mean_reversion"
    assert calls[0]["tickers"] == ()


def test_pipeline_accepts_positional_mode_screener_and_ticker(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

    result = runner.invoke(app, ["pipeline", "single", "mr", "BBCA", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls[0]["mode"] == "single"
    assert calls[0]["screener_mode"] == "mean_reversion"
    assert calls[0]["tickers"] == ("BBCA",)


def test_pipeline_choose_selects_modes_interactively(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

    result = runner.invoke(app, ["pipeline", "choose", "--dry-run"], input="2\n2\n")

    assert result.exit_code == 0, result.output
    assert calls[0]["mode"] == "single"
    assert calls[0]["screener_mode"] == "mean_reversion"


def test_pipeline_choose_rejects_no_interactive(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

    result = runner.invoke(app, ["pipeline", "choose", "--no-interactive"])

    assert result.exit_code != 0
    assert calls == []
    assert "choose cannot be used with --no-interactive" in result.output


def test_pipeline_uses_global_verbose_flag(monkeypatch):
    calls = []

    def fake_run_pipeline_cli(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(
        "app.cli.commands.pipeline.run_pipeline_cli", fake_run_pipeline_cli
    )

    result = runner.invoke(app, ["--verbose", "pipeline", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert calls[0]["verbose"] is True


def test_pipeline_runner_passes_argparse_argv_without_program_name(monkeypatch):
    calls = []

    def fake_run_cli(argv):
        calls.append(argv)

    monkeypatch.setitem(
        sys.modules, "orchestrator", SimpleNamespace(_run_cli=fake_run_cli)
    )

    run_pipeline_cli(
        dry_run=True,
        output_dir=Path("tmp/out"),
        tickers=("BBRI", "BBCA"),
        skip_scraping=True,
        no_interactive=True,
        mode="multi",
        screener_mode="momentum",
        verbose=True,
    )

    assert calls == [
        [
            "--output-dir",
            str(Path("tmp/out")),
            "--mode",
            "multi",
            "--screener-mode",
            "momentum",
            "--dry-run",
            "--skip-scraping",
            "--no-interactive",
            "--verbose",
            "--tickers",
            "BBRI",
            "BBCA",
        ]
    ]


def _probe_orchestrator_pipeline_reasoning(
    monkeypatch, tickers: list[str], provider: str = "codex"
):
    import orchestrator

    calls = []

    class FakeChatCodexResponses:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    args = SimpleNamespace(
        verbose=False,
        details=False,
        output_dir="output",
        scrape_cmd=None,
        no_interactive=True,
        skip_scraping=True,
        dry_run=True,
        tickers=tickers,
    )

    async def fake_main(**_kwargs):
        from providers.codex_adapter import get_codex_flash_llm, get_codex_pro_llm

        get_codex_flash_llm()
        get_codex_pro_llm()

    monkeypatch.setattr(
        orchestrator,
        "settings",
        SimpleNamespace(DEFAULT_LLM_PROVIDER=provider),
    )
    monkeypatch.setattr(orchestrator._pipeline, "_ensure_utf8_stdout", lambda: None)
    monkeypatch.setattr(orchestrator._pipeline, "_parse_cli_args", lambda _argv: args)
    monkeypatch.setattr(orchestrator._pipeline, "configure_cli_logging", lambda **_: None)
    monkeypatch.setattr(
        orchestrator._pipeline,
        "_cli_renderer",
        SimpleNamespace(show_details=False),
    )
    monkeypatch.setattr(orchestrator, "configure_output_dir", lambda _path: None)
    monkeypatch.setattr(
        orchestrator._pipeline,
        "_cli",
        SimpleNamespace(run=lambda **_kwargs: True),
    )
    monkeypatch.setattr(orchestrator._pipeline, "main", fake_main)
    monkeypatch.setattr("providers.codex_adapter.resolve_codex_token", lambda: "t")
    monkeypatch.setattr(
        "providers.codex_adapter.settings",
        SimpleNamespace(
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_FLASH_REASONING_EFFORT="medium",
            CODEX_PRO_MODEL="gpt-5.5",
            CODEX_PRO_REASONING_EFFORT="high",
        ),
    )
    monkeypatch.setattr(
        "providers.codex_responses_llm.ChatCodexResponses",
        FakeChatCodexResponses,
    )

    orchestrator._run_cli(["--dry-run"])

    return [call["reasoning_effort"] for call in calls]


def test_pipeline_without_explicit_tickers_disables_codex_reasoning(monkeypatch):
    assert _probe_orchestrator_pipeline_reasoning(monkeypatch, []) == [None, None]


def test_pipeline_with_three_explicit_tickers_keeps_codex_reasoning(monkeypatch):
    assert _probe_orchestrator_pipeline_reasoning(
        monkeypatch, ["BBCA", "BBRI", "TLKM"]
    ) == ["medium", "high"]


def test_pipeline_with_more_than_three_explicit_tickers_disables_codex_reasoning(
    monkeypatch,
):
    assert _probe_orchestrator_pipeline_reasoning(
        monkeypatch, ["BBCA", "BBRI", "TLKM", "ASII"]
    ) == [None, None]


def test_pipeline_non_codex_provider_never_applies_reasoning_override(monkeypatch):
    # Gating regression: with gemini active, _run_cli must leave the Codex
    # reasoning ContextVar untouched (efforts come straight from settings).
    assert _probe_orchestrator_pipeline_reasoning(
        monkeypatch, [], provider="gemini"
    ) == ["medium", "high"]


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


def test_run_debate_cli_does_not_apply_pipeline_reasoning_override(monkeypatch):
    calls = []

    async def fake_main(argv):
        calls.append(argv)

    def forbidden_override(**_kwargs):
        raise AssertionError("idx debate must not use the pipeline reasoning override")

    monkeypatch.setitem(sys.modules, "run_debate", SimpleNamespace(main=fake_main))
    monkeypatch.setattr(
        "providers.codex_adapter.codex_reasoning_override",
        forbidden_override,
    )

    from app.cli.commands.debate import run_debate_cli

    run_debate_cli(
        tickers=["BBRI"],
        output_dir=Path("output/debates"),
        verbose=False,
        details=True,
    )

    assert calls == [["--tickers", "BBRI", "--output-dir", str(Path("output/debates"))]]


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
