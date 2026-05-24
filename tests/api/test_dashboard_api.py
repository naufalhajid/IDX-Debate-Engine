import json
import sys
import types

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
    valid_random = client.get("/api/validate-key", headers={"X-Gemini-API-Key": "some-random-key-123"})

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
                        "target_price": 2480,
                        "stop_loss": 2200,
                        "risk_reward_ratio": 1.95,
                        "summary": "Ringkasan verdict.",
                    },
                    "risk_governor": {
                        "sizing_allowed": True,
                        "entry_low": 2260,
                        "entry_high": 2330,
                        "target_price": 2480,
                        "stop_loss": 2200,
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
                        {"role": "devils_advocate", "content": "Stress test", "round": 2},
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


def test_debate_stream_uses_stream_run(monkeypatch):
    class FakeDebateChamber:
        async def stream_run(self, ticker):
            yield {"type": "progress", "ticker": ticker, "phase": "scouting", "pct": 10}
            yield {"type": "done", "ticker": ticker}

    monkeypatch.setitem(
        sys.modules,
        "services.debate_chamber",
        types.SimpleNamespace(DebateChamber=FakeDebateChamber),
    )

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
    assert "data: [DONE]" in body
