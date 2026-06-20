from services.fair_value_calculator import (
    FairValueCalculator, KeyStats,
    refresh_sector_benchmarks, _SECTOR_REPRESENTATIVE_TICKERS,
)
from services.stockbit_api_client import StockbitApiClient
import json

# ── FV-5: Refresh semua sektor ────────────────────────────────────────────────
client = StockbitApiClient()

def _fetch(ticker: str) -> dict:
    return client.get(
        f"https://exodus.stockbit.com/keystats/ratio/v1/{ticker}?year_limit=10"
    )

print("Fetching sector benchmarks for:", list(_SECTOR_REPRESENTATIVE_TICKERS.keys()))
benchmarks = refresh_sector_benchmarks(_fetch)   # tanpa sectors= → semua sektor
print("\nCached sector benchmarks:")
print(json.dumps(benchmarks, indent=2))
print()

from services.fair_value_calculator import FairValueCalculator, KeyStats

stats = KeyStats(
    ticker="BBRI", current_price=5000, eps_ttm=400,
    book_value_per_share=3000, roe=0.15,
    historical_pe_avg=14, historical_pb_avg=2.5,
)
r = FairValueCalculator(stats, sector="bank").fair_value_weighted()
print("BBRI (SOE):")
print("  fair_value              =", r["fair_value"])
print("  is_soe                  =", r["is_soe"])
print("  governance_discount_pct =", r["governance_discount_pct"])
print("  keystats_stale          =", r["keystats_stale"])
print("  keystats_age_days       =", r["keystats_age_days"])
print("  valuation_verdict       =", r["valuation_verdict"])
print()

stats2 = KeyStats(
    ticker="BBCA", current_price=5000, eps_ttm=400,
    book_value_per_share=3000, roe=0.15,
    historical_pe_avg=14, historical_pb_avg=2.5,
)
r2 = FairValueCalculator(stats2, sector="bank").fair_value_weighted()
print("BBCA (non-SOE):")
print("  fair_value              =", r2["fair_value"])
print("  is_soe                  =", r2["is_soe"])
print("  governance_discount_pct =", r2["governance_discount_pct"])
print()

pct = round((r["fair_value"] / r2["fair_value"]) * 100, 1)
print(f"BBRI FV = {pct}% of BBCA FV  (expected ~85%)")
