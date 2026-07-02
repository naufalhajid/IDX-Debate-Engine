# Screener Signal IC Validation

Generated: 2026-07-02
Snapshots: 19
Forward horizon: fundamental = next XLSX snapshot; technical = 5 trading days (yfinance panel)
HLZ threshold: abs(t-stat) >= 2.57 AND mean IC > 0.05
BH correction applied across the combined signal family.

| Signal | Family | Periods | Mean IC | t-stat | p-value | BH q-value | HLZ pass | FDR pass |
|---|---|---:|---:|---:|---:|---:|---|---|
| price_to_equity_discount | fundamental | 16 | -0.023239 | -1.475404 | 0.140104 | 0.33482 | False | False |
| pbv_x_roe | fundamental | 16 | 0.01758 | 1.287682 | 0.197857 | 0.33482 | False | False |
| relative_pe_inverse | fundamental | 16 | -0.015736 | -1.279014 | 0.200892 | 0.33482 | False | False |
| eps_growth | fundamental | 16 | 0.00563 | 0.574842 | 0.565398 | 0.565398 | False | False |
| yearly_price_change | fundamental | 0 | - | - | - | - | False | False |
| composite_rank_inverse | fundamental | 16 | -0.048583 | -2.714675 | 0.006634 | 0.06634 | False | False |
| rsi14 | technical | 18 | -0.028842 | -0.94408 | 0.345129 | 0.431411 | False | False |
| rsi_score | technical | 18 | 0.017804 | 0.637211 | 0.523987 | 0.565398 | False | False |
| vol_surge | technical | 12 | -0.147614 | -1.469434 | 0.141715 | 0.33482 | False | False |
| vol_score | technical | 12 | -0.106492 | -1.363316 | 0.172783 | 0.33482 | False | False |
| price_mom_22d | technical | 18 | -0.047629 | -0.994139 | 0.320155 | 0.431411 | False | False |
