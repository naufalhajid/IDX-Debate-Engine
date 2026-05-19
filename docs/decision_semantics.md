# Decision Semantics

This project separates signal quality from deployability. Keep that separation
clear in reports and future code changes.

## Core Labels

| Field | Meaning |
| --- | --- |
| `rating` | CIO verdict such as `BUY`, `STRONG_BUY`, `HOLD`, or `AVOID`. |
| `confidence` | CIO confidence in the verdict, from `0.0` to `1.0`. |
| `consensus_reached` | Whether the chamber reached a consensus state. |
| `consensus_method` | How the final decision was resolved, such as `soft_hold` or `confidence_winner`. |
| `dissenting_agents` | Agents whose positions disagreed with the final direction or timing. |
| `risk_governor.status` | Deterministic actionability status after price, entry, target, and stop checks. |
| `risk_governor.sizing_allowed` | Whether the candidate may be passed into position sizing. |
| `wait_and_see` | CIO-level caution flag. It can coexist with a technically valid trade envelope. |
| `outcome` | Backtest memory state: `open` means not yet evaluable, while `win` / `loss` are auto-evaluated price outcomes. |
| `evaluation_reason` | Why a backtest record was labeled, such as `target_hit`, `stop_hit`, or `horizon_close_above_entry`. |

## Reporting Rules

Use these rules when reviewing or changing reports:

1. `full_batch_results.json` is the authority for structured fields.
2. `TOP_3_SWING_TRADES.md` is a presentation layer and must not contradict the JSON.
3. A non-deployable idea can remain visible as a setup, but it must not look like a market-buy instruction.
4. `sizing_allowed = true` does not guarantee that a position will be allocated. Lot size, capital, max position cap, and stop-risk budget can still result in zero lots.
5. If the report shows `BUY` but the CIO summary says `HOLD`, treat it as a report consistency issue to fix before using the output for decisions.
6. If allocated position is zero, the report should state the portfolio constraint explicitly instead of implying that sizing disappeared.
7. Realized outcome learning should use `win` / `loss` records from backtest memory before falling back to debate-history consistency.
