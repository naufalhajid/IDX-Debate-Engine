import logging

from services.context_pack_builder import (
    CONTEXT_FIELD_TIERS,
    CONTEXT_CHAR_LIMIT,
    MAX_PROMPT_CHARS,
    build_context_pack,
    pack_to_prompt_string,
)


def test_build_context_pack_full_data_populates_all_fields() -> None:
    raw_data = {
        "as_of": "2026-05-12T10:00:00+00:00",
        "current_price": 2600,
        "fair_value_estimate": 2919,
        "fundamentals": {"roe": 0.12, "net_margin": 0.22},
        "technical_indicators": {"ma50": 2375, "rsi14": 66.7},
        "sentiment_summary": "INSUFFICIENT_DATA but no red flags.",
        "data_sources": ["stockbit", "yfinance", "gemini"],
    }

    pack = build_context_pack("ADRO", raw_data)

    assert pack.ticker == "ADRO"
    assert pack.price == 2600.0
    assert pack.fair_value == 2919.0
    assert pack.fundamentals == {"roe": 0.12, "net_margin": 0.22}
    assert pack.technicals == {"ma50": 2375, "rsi14": 66.7}
    assert pack.sentiment_summary == "INSUFFICIENT_DATA but no red flags."
    assert pack.data_sources == ["stockbit", "yfinance", "gemini"]
    assert pack.missing_fields == []
    assert pack.token_estimate == len(pack_to_prompt_string(pack)) // 4


def test_build_context_pack_partial_data_lists_missing_fields() -> None:
    pack = build_context_pack("BBCA", {"close": 9000})

    assert pack.price == 9000.0
    assert pack.fair_value is None
    assert pack.fundamentals == {}
    assert pack.technicals == {}
    assert pack.sentiment_summary is None
    assert pack.data_sources == []
    assert pack.missing_fields == [
        "fair_value",
        "fundamentals",
        "technicals",
        "sentiment_summary",
        "data_sources",
    ]


def test_pack_to_prompt_string_truncates_oversized_fundamentals(
    caplog,
) -> None:
    raw_data = {
        "price": 1000,
        "fair_value": 1300,
        "fundamentals": {field: "x" * 800 for field in CONTEXT_FIELD_TIERS["tier2"]},
        "technicals": {"ma50": 980},
        "sentiment_summary": "Neutral.",
        "data_sources": ["stockbit"],
        "rag_evidence": "r" * 3000,
        "analyst_notes": "a" * 3000,
    }

    caplog.set_level(logging.WARNING, logger="services.context_pack_builder")
    pack = build_context_pack("TLKM", raw_data)
    prompt = pack_to_prompt_string(pack)

    assert len(prompt) <= MAX_PROMPT_CHARS
    assert "Tier1 Core Fields:" in prompt
    assert "[truncated:" in prompt
    assert "fields truncated" in caplog.text


def test_pack_to_prompt_string_small_limit_preserves_tier1() -> None:
    raw_data = {
        "current_price": 1000,
        "fair_value": 1300,
        "verdict": {
            "rating": "BUY",
            "confidence": 0.61,
            "risk_reward_ratio": 2.0,
            "entry_low": 950,
            "entry_high": 1000,
            "target_price": 1150,
            "stop_loss": 900,
        },
        "fundamentals": {field: "x" * 500 for field in CONTEXT_FIELD_TIERS["tier2"]},
        "news_summary": "n" * 1000,
        "rag_evidence": "r" * 1000,
        "analyst_notes": "a" * 1000,
    }
    pack = build_context_pack("TEST", raw_data)
    prompt = pack_to_prompt_string(pack, char_limit=120)

    tier1_section = prompt.splitlines()[2]
    for field_name in CONTEXT_FIELD_TIERS["tier1"]:
        assert field_name in tier1_section
    assert "[truncated:" in prompt
    assert CONTEXT_CHAR_LIMIT == MAX_PROMPT_CHARS
