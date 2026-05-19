from datetime import datetime

from schemas import BaseDataClass


class KeyAnalysis(BaseDataClass):
    normal_price: float = 0.0
    price_to_equity_discount: float = 0.0
    relative_pe_ratio_ttm: float = 0.0
    eps_growth: float = 0.0
    debt_to_total_assets_ratio: float = 0.0
    liquidity_differential: float = 0.0
    cce: float = 0.0
    operating_efficiency: float = 0.0
    dividend_payout_efficiency: float = 0.0
    yearly_price_change: float = 0.0
    composite_rank: float = 0.0
    net_debt_to_equity_ratio: float = 0.0
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
