import json
import sys
import types

import pytest

from fastapi.testclient import TestClient

from app.api.cache import get_batch_cache
from app.api.main import app
from app.api.routers import stocks as stocks_router


client = TestClient(app)


def _reset_results_path(monkeypatch, path):
    get_batch_cache().clear()
    monkeypatch.setattr(stocks_router, "RESULTS_PATH", path)
    stocks_router.invalidate_results_cache()


def test_health_reports_results_presence(monkeypatch, tmp_path):
    results_path = tmp_path / "full_batch_results.json"
    _reset_results_path(monkeypatch, results_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["results_exist"] is False


def test_validate_key_behavior(monkeypatch):
    class FakeLLM:
        async def ainvoke(self, prompt, **kwargs):
            return "pong"

    from providers import gemini

    monkeypatch.setattr(gemini, "get_flash_llm", lambda: FakeLLM())

    missing = client.get("/api/validate-key")
    empty = client.get("/api/validate-key", headers={"X-Gemini-API-Key": "   "})
    valid_random = client.get(
        "/api/validate-key", headers={"X-Gemini-API-Key": "some-random-key-123"}
    )

    assert missing.status_code == 401
    assert missing.json()["detail"]["code"] == "MISSING_API_KEY"

    assert empty.status_code == 401
    assert empty.json()["detail"]["code"] == "MISSING_API_KEY"

    assert valid_random.status_code == 200
    assert valid_random.json() == {"valid": True}


def test_results_missing_file_returns_no_results(monkeypatch, tmp_path):
    _reset_results_path(monkeypatch, tmp_path / "missing.json")

    response = client.get("/api/results")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NO_RESULTS"


def test_results_normalizes_orchestrator_artifact(monkeypatch, tmp_path):
    results_path = tmp_path / "full_batch_results.json"
    results_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "ADRO",
                    "sector_key": "energy",
                    "conviction_score": 0.72,
                    "status": "success",
                    "news_sentiment": "UNKNOWN",
                    "news_confidence_adjustment": 0,
                    "verdict": {
                        "ticker": "ADRO",
                        "rating": "BUY",
                        "confidence": 0.72,
                        "entry_price_range": "2260 - 2330",
                        "target_price": 2590,
                        "stop_loss": 2200,
                        "risk_reward_ratio": 2.0,
                        "execution_horizon_days": 10,
                        "summary": "Ringkasan verdict.",
                    },
                    "risk_governor": {
                        "status": "deployable",
                        "sizing_allowed": True,
                        "entry_low": 2260,
                        "entry_high": 2330,
                        "target_price": 2590,
                        "stop_loss": 2200,
                    },
                    "position_sizing": {
                        "lot": 2,
                        "shares": 200,
                        "max_loss_rp": 26000,
                    },
                    "debate_history": [
                        {
                            "role": "bull",
                            "content": "Bull case",
                            "round": 1,
                            "confidence": 0.8,
                        },
                        {
                            "role": "bear",
                            "content": "Bear case",
                            "round": 1,
                            "confidence": 0.6,
                        },
                        {
                            "role": "devils_advocate",
                            "content": "Stress test",
                            "round": 2,
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    _reset_results_path(monkeypatch, results_path)

    response = client.get("/api/results")

    assert response.status_code == 200
    payload = response.json()
    assert payload[0]["ticker"] == "ADRO"
    assert payload[0]["conviction_score"] == 72
    assert payload[0]["entry_low"] == 2260
    assert payload[0]["actionable"] is True
    assert payload[0]["debate_rounds"][0]["score_delta"] == 20
    assert payload[0]["devil_advocate_triggered"] is True


def test_results_skips_artifact_with_invalid_ticker(monkeypatch, tmp_path):
    results_path = tmp_path / "full_batch_results.json"
    results_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "../escape",
                    "verdict": {"ticker": "../escape", "rating": "HOLD"},
                },
                {
                    "ticker": "bbca.jk",
                    "verdict": {"ticker": "bbca.jk", "rating": "HOLD"},
                },
            ]
        ),
        encoding="utf-8",
    )
    _reset_results_path(monkeypatch, results_path)

    response = client.get("/api/results")

    assert response.status_code == 200
    assert [item["ticker"] for item in response.json()] == ["BBCA"]


def test_results_rejects_cross_stock_artifact_identity(monkeypatch, tmp_path):
    results_path = tmp_path / "full_batch_results.json"
    results_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "BBCA",
                    "verdict": {"ticker": "BMRI", "rating": "BUY"},
                },
                {
                    "ticker": "TLKM",
                    "verdict": {"ticker": "TLKM", "rating": "HOLD"},
                },
            ]
        ),
        encoding="utf-8",
    )
    _reset_results_path(monkeypatch, results_path)

    response = client.get("/api/results")

    assert response.status_code == 200
    assert [item["ticker"] for item in response.json()] == ["TLKM"]


def test_debate_filename_identity_must_match_payload(monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    debates_dir = output_root / "debates"
    debates_dir.mkdir(parents=True)
    (debates_dir / "BBCA_debate.json").write_text(
        json.dumps(
            {
                "ticker": "BMRI",
                "verdict": {"ticker": "BMRI", "rating": "BUY"},
            }
        ),
        encoding="utf-8",
    )
    _reset_results_path(monkeypatch, output_root / "full_batch_results.json")

    response = client.get("/api/results")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NO_RESULTS"


def test_health_excludes_invalid_and_mismatched_artifacts(monkeypatch, tmp_path):
    output_root = tmp_path / "output"
    debates_dir = output_root / "debates"
    debates_dir.mkdir(parents=True)
    artifacts = {
        "BBCA_debate.json": {
            "ticker": "BBCA",
            "verdict": {"ticker": "BBCA", "rating": "HOLD"},
        },
        "BMRI_debate.json": {
            "ticker": "TLKM",
            "verdict": {"ticker": "TLKM", "rating": "BUY"},
        },
        "BAPA_debate.json": {
            "ticker": "../escape",
            "verdict": {"ticker": "../escape", "rating": "BUY"},
        },
    }
    for filename, payload in artifacts.items():
        (debates_dir / filename).write_text(json.dumps(payload), encoding="utf-8")
    _reset_results_path(monkeypatch, output_root / "full_batch_results.json")

    response = client.get("/api/health")

    assert response.status_code == 200
    stats = response.json()["debate_stats"]
    assert stats["total_debates"] == 1
    assert stats["ratings_distribution"]["HOLD"] == 1
    assert stats["ratings_distribution"]["BUY"] == 0


def test_http_ticker_guard_runs_before_database_dependency():
    calls = 0

    async def unexpected_db_dependency():
        nonlocal calls
        calls += 1
        yield object()

    app.dependency_overrides[stocks_router.get_db] = unexpected_db_dependency
    try:
        response = client.get("/api/stocks/A:B")
    finally:
        app.dependency_overrides.pop(stocks_router.get_db, None)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_IDX_TICKER"
    assert calls == 0


def test_api_scanner_does_not_follow_valid_named_symlink_outside_root(
    monkeypatch,
    tmp_path,
):
    output_root = tmp_path / "output"
    debates_dir = output_root / "debates"
    outside_dir = tmp_path / "outside"
    debates_dir.mkdir(parents=True)
    outside_dir.mkdir()
    outside_file = outside_dir / "external.json"
    outside_file.write_text(
        json.dumps({"ticker": "BBCA", "verdict": {"rating": "BUY"}}),
        encoding="utf-8",
    )
    linked_file = debates_dir / "BBCA_debate.json"
    try:
        linked_file.symlink_to(outside_file)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlink unavailable on this platform: {exc}")

    _reset_results_path(monkeypatch, output_root / "full_batch_results.json")

    assert stocks_router._safe_debate_files() == []


def test_results_prefers_merged_ticker_state_when_present(monkeypatch, tmp_path):
    results_path = tmp_path / "full_batch_results.json"
    merged_path = tmp_path / "merged_batch_results.json"
    results_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "LAST",
                    "verdict": {"ticker": "LAST", "rating": "HOLD"},
                }
            ]
        ),
        encoding="utf-8",
    )
    merged_path.write_text(
        json.dumps(
            [
                {
                    "ticker": "HIST",
                    "verdict": {"ticker": "HIST", "rating": "BUY"},
                }
            ]
        ),
        encoding="utf-8",
    )
    _reset_results_path(monkeypatch, results_path)

    response = client.get("/api/results")

    assert response.status_code == 200
    payload = response.json()
    assert [item["ticker"] for item in payload] == ["HIST"]


def test_debate_stream_uses_stream_run(monkeypatch):
    from core.orchestrator import pipeline

    class FakeDebateChamber:
        async def stream_run(self, ticker):
            yield {"type": "progress", "ticker": ticker, "phase": "scouting", "pct": 10}
            yield {"type": "done", "ticker": ticker}

    async def fake_orchestrator_main(**kwargs):
        chamber = kwargs["chamber_factory"]()
        await chamber.run("BBCA")
        return []

    async def fake_load_results():
        return {"BBCA": {"ticker": "BBCA", "rating": "HOLD"}}

    monkeypatch.setitem(
        sys.modules,
        "services.debate_chamber",
        types.SimpleNamespace(DebateChamber=FakeDebateChamber),
    )
    monkeypatch.setattr(pipeline, "main", fake_orchestrator_main)
    monkeypatch.setattr(stocks_router, "_load_results", fake_load_results)

    with client.stream(
        "POST",
        "/api/debate/stream",
        json={"tickers": ["bbca"]},
        headers={"X-Gemini-API-Key": "AIza-test"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert '"ticker": "BBCA"' in body
    assert '"type": "progress"' in body
    assert '"phase": "scouting"' in body
    assert "data: [DONE]" in body


def test_debate_stream_forwards_dashboard_config(monkeypatch):
    from core.orchestrator import pipeline

    captured = {}

    async def fake_orchestrator_main(**kwargs):
        captured.update(kwargs)
        return []

    async def fake_load_results():
        return {
            "BBCA": {
                "ticker": "BBCA",
                "sector": "bank",
                "conviction_score": 75,
                "rating": "BUY",
                "actionable": True,
                "target_price": 9800,
                "stop_loss": 8800,
                "entry_low": 9000,
                "entry_high": 9200,
                "risk_reward": 2.0,
                "debate_rounds": [],
                "scout_metrics": {
                    "technical": {},
                    "fundamental": {},
                    "sentiment": {},
                },
                "devil_advocate_triggered": False,
                "verdict_summary": "Final verdict.",
                "last_debated_at": "2026-05-26",
            }
        }

    monkeypatch.setattr(pipeline, "main", fake_orchestrator_main)
    monkeypatch.setattr(stocks_router, "_load_results", fake_load_results)

    with client.stream(
        "POST",
        "/api/debate/stream",
        json={
            "tickers": ["bbca"],
            "total_capital": 100_000_000,
            "max_loss_pct": 0.01,
            "max_positions": 3,
        },
        headers={"X-Gemini-API-Key": "AIza-test"},
    ) as response:
        body = response.read().decode("utf-8")

    assert response.status_code == 200
    assert captured["tickers"] == ["BBCA"]
    assert captured["user_config"] == {
        "total_capital": 100_000_000.0,
        "max_loss_pct": 0.01,
        "max_positions": 3,
    }
    assert '"stage": "final"' in body

