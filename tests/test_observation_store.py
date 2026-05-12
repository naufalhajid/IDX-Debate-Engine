from pathlib import Path

from core.observation_store import AgentObservation, ObservationStore


def _observation(
    *,
    run_id: str = "run-1",
    ticker: str = "BBCA",
    agent: str = "bull",
) -> AgentObservation:
    return AgentObservation(
        run_id=run_id,
        ticker=ticker,
        agent=agent,
        position="BUY",
        confidence=0.82,
        summary="Constructive setup with improving momentum.",
        round_num=1,
        prompt_version="prompt-v1",
        timestamp="2026-05-13T10:00:00+07:00",
        evidence=["MA50 support holds", "Foreign flow positive"],
    )


def test_append_and_query_by_ticker_returns_correct_records(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.jsonl")
    store.append(_observation(ticker="BBCA"))
    store.append(_observation(ticker="BBRI"))

    results = store.query(ticker="BBCA")

    assert len(results) == 1
    assert results[0].ticker == "BBCA"
    assert results[0].agent == "bull"


def test_query_by_agent_filters_correctly(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.jsonl")
    store.append(_observation(agent="bull"))
    store.append(_observation(agent="bear"))

    results = store.query(agent="bear")

    assert len(results) == 1
    assert results[0].agent == "bear"


def test_latest_run_id_returns_last_appended_run_id(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.jsonl")
    store.append(_observation(run_id="run-1"))
    store.append(_observation(run_id="run-2"))

    assert store.latest_run_id() == "run-2"


def test_query_with_no_filters_returns_all_records(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.jsonl")
    store.append(_observation(ticker="BBCA"))
    store.append(_observation(ticker="BBRI"))

    results = store.query()

    assert len(results) == 2
    assert [result.ticker for result in results] == ["BBCA", "BBRI"]
