from __future__ import annotations

from services import macro_refresh


def test_get_live_sbn_10y_refreshes_when_requested(monkeypatch):
    monkeypatch.setattr(macro_refresh, "load_cached_macro_rates", lambda: None)
    monkeypatch.setattr(
        macro_refresh,
        "refresh_macro_rates",
        lambda stockbit_client=None: {
            "sbn_10y": 0.081,
            "deposit_rate": None,
            "source": "test",
            "fetched_at": "2026-06-24T00:00:00+00:00",
        },
    )

    assert macro_refresh.get_live_sbn_10y(refresh_if_stale=True) == 0.081
