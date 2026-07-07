"""Arm B — single-agent verdict + deterministic gates (risk_governor).

Menguji hipotesis V2.1 (docs/research/ablation_v2_1_structural_report.md): sebagian
besar "nilai tambah" debat atas baseline single-agent berasal dari gate deterministik
(risk_governor) yang secara struktural HANYA terpasang di jalur multi-agent — bukan dari
penalaran LLM yang lebih kaya. `services/single_agent_analyzer.py` saat ini MELEWATI
risk_governor (dikonfirmasi V2.1 report baris 72), jadi verdict BUY-nya tak pernah kena
gate yang sama. Modul ini menerapkan `core/risk_governor.evaluate_risk` di atas verdict
single-agent supaya bisa dibandingkan adil (Arm B) melawan full-debate (Arm C) dan
quant-only (Arm A) di harness forward-outcome.

FIDELITY NOTE (v1): menerapkan `evaluate_risk` (R/R floor tier-aware, validitas entry,
overvaluation, ARA, likuiditas). BELUM mereplikasi gate trade-envelope debate_chamber
(`no_momentum_confirmation`, `stop_inside_noise`) yang butuh technicals dihitung ulang di
sini — celah terdokumentasi untuk v2. Karena itu Arm B saat ini = "single-agent + gate
risk_governor", superset dari single-agent telanjang tapi subset dari gate penuh debate.
"""

from __future__ import annotations

from core.risk_governor import RiskDecision, evaluate_risk
from services.single_agent_analyzer import SingleAgentVerdict


def gate_single_agent_verdict(verdict: SingleAgentVerdict) -> RiskDecision:
    """Terapkan risk_governor.evaluate_risk pada verdict single-agent.

    Verdict single-agent sudah membawa entry_price_range/target_price/stop_loss/
    risk_reward_ratio/rating/confidence/current_price — persis kontrak yang dibaca
    evaluate_risk (candidate["verdict"]).
    """
    candidate = {
        "ticker": verdict.ticker,
        "current_price": verdict.current_price,
        "verdict": verdict.model_dump(),
    }
    return evaluate_risk(candidate)


def is_gated_buy(verdict: SingleAgentVerdict) -> bool:
    """Arm B BUY = single-agent memberi rating BUY DAN gate deterministik mengizinkan sizing."""
    if verdict.rating != "BUY":
        return False
    return gate_single_agent_verdict(verdict).sizing_allowed
