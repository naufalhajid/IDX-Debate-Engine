from datetime import datetime

from pydantic import Field

from schemas import BaseDataClass


class CurrentValuation(BaseDataClass):
    current_pe_ratio_annual: float = 0.0
    current_pe_ratio_ttm: float = 0.0
    forward_pe_ratio: float = 0.0
    ihsg_pe_ratio_ttm_median: float = 0.0
    earnings_yield_ttm: float = 0.0
    current_price_to_sales_ttm: float = 0.0
    current_price_to_book_value: float = 0.0
    current_price_to_cashflow_ttm: float = 0.0
    current_price_to_free_cashflow_ttm: float = 0.0
    ev_to_ebit_ttm: float = 0.0
    ev_to_ebitda_ttm: float = 0.0
    peg_ratio: float = 0.0
    peg_ratio_3yr: float = 0.0
    peg_forward: float = 0.0


class PerShare(BaseDataClass):
    current_eps_ttm: float = 0
    current_eps_annualised: float = 0
    revenue_per_share_ttm: float = 0
    cash_per_share_quarter: float = 0
    current_book_value_per_share: float = 0
    free_cashflow_per_share_ttm: float = 0


class Solvency(BaseDataClass):
    current_ratio_quarter: float = 0
    quick_ratio_quarter: float = 0
    debt_to_equity_ratio_quarter: float = 0
    lt_debt_equity_quarter: float = 0
    total_liabilities_equity_quarter: float = 0
    total_debt_total_assets_quarter: float = 0
    financial_leverage_quarter: float = 0
    interest_rate_coverage_ttm: float = 0
    free_cash_flow_quarter: float = 0
    altman_z_score_modified: float = 0


class ManagementEffectiveness(BaseDataClass):
    return_on_assets_ttm: float = 0
    return_on_equity_ttm: float = 0
    return_on_capital_employed_ttm: float = 0
    return_on_invested_capital_ttm: float = 0
    days_sales_outstanding_quarter: float = 0
    days_inventory_quarter: float = 0
    days_payables_outstanding_quarter: float = 0
    cash_conversion_cycle_quarter: float = 0
    receivables_turnover_quarter: float = 0
    asset_turnover_ttm: float = 0
    inventory_turnover_ttm: float = 0


class Profitability(BaseDataClass):
    gross_profit_margin_quarter: float = 0.0
    operating_profit_margin_quarter: float = 0.0
    net_profit_margin_quarter: float = 0.0


class Growth(BaseDataClass):
    revenue_quarter_yoy_growth: float = 0.0
    gross_profit_quarter_yoy_growth: float = 0.0
    net_income_quarter_yoy_growth: float = 0.0


class Dividend(BaseDataClass):
    dividend: float = 0.0
    dividend_ttm: float = 0.0
    payout_ratio: float = 0.0
    dividend_yield: float = 0.0
    latest_dividend_ex_date: str = ""


class MarketRank(BaseDataClass):
    piotroski_f_score: float = 0.0
    eps_rating: float = 0.0
    relative_strength_rating: float = 0.0
    rank_market_cap: float = 0.0
    rank_current_pe_ratio_ttm: float = 0.0
    rank_earnings_yield: float = 0.0
    rank_p_s: float = 0.0
    rank_p_b: float = 0.0
    rank_near_52_weeks_high: float = 0.0


class IncomeStatement(BaseDataClass):
    revenue_ttm: float = 0.0
    gross_profit_ttm: float = 0.0
    ebitda_ttm: float = 0.0
    net_income_ttm: float = 0.0


class BalanceSheet(BaseDataClass):
    cash_quarter: float = 0.0
    total_assets_quarter: float = 0.0
    total_liabilities_quarter: float = 0.0
    working_capital_quarter: float = 0.0
    total_equity: float = 0.0
    long_term_debt_quarter: float = 0.0
    short_term_debt_quarter: float = 0.0
    total_debt_quarter: float = 0.0
    net_debt_quarter: float = 0.0


class CashFlowStatement(BaseDataClass):
    cash_from_operations_ttm: float = 0.0
    cash_from_investing_ttm: float = 0.0
    cash_from_financing_ttm: float = 0.0
    capital_expenditure_ttm: float = 0.0
    free_cash_flow_ttm: float = 0.0


class PricePerformance(BaseDataClass):
    one_week_price_returns: float = 0.0
    three_month_price_returns: float = 0.0
    one_month_price_returns: float = 0.0
    six_month_price_returns: float = 0.0
    one_year_price_returns: float = 0.0
    three_year_price_returns: float = 0.0
    five_year_price_returns: float = 0.0
    ten_year_price_returns: float = 0.0
    year_to_date_price_returns: float = 0.0
    fifty_two_week_high: float = 0.0
    fifty_two_week_low: float = 0.0


class Stat(BaseDataClass):
    current_share_outstanding: float = 0.0
    market_cap: float = 0.0
    enterprise_value: float = 0.0


class Fundamental(BaseDataClass):
    stat: Stat = Field(default_factory=Stat)
    current_valuation: CurrentValuation = Field(default_factory=CurrentValuation)
    per_share: PerShare = Field(default_factory=PerShare)
    solvency: Solvency = Field(default_factory=Solvency)
    management_effectiveness: ManagementEffectiveness = Field(
        default_factory=ManagementEffectiveness
    )
    profitability: Profitability = Field(default_factory=Profitability)
    growth: Growth = Field(default_factory=Growth)
    dividend: Dividend = Field(default_factory=Dividend)
    market_rank: MarketRank = Field(default_factory=MarketRank)
    income_statement: IncomeStatement = Field(default_factory=IncomeStatement)
    balance_sheet: BalanceSheet = Field(default_factory=BalanceSheet)
    cash_flow_statement: CashFlowStatement = Field(default_factory=CashFlowStatement)
    price_performance: PricePerformance = Field(default_factory=PricePerformance)
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()

    @classmethod
    def from_orm(cls, orm_obj):
        return cls(
            stat=Stat.from_orm(orm_obj.stat),
            current_valuation=CurrentValuation.from_orm(orm_obj.current_valuation),
            per_share=PerShare.from_orm(orm_obj.per_share),
            solvency=Solvency.from_orm(orm_obj.solvency),
            management_effectiveness=ManagementEffectiveness.from_orm(
                orm_obj.management_effectiveness
            ),
            profitability=Profitability.from_orm(orm_obj.profitability),
            growth=Growth.from_orm(orm_obj.growth),
            dividend=Dividend.from_orm(orm_obj.dividend),
            market_rank=MarketRank.from_orm(orm_obj.market_rank),
            income_statement=IncomeStatement.from_orm(orm_obj.income_statement),
            balance_sheet=BalanceSheet.from_orm(orm_obj.balance_sheet),
            cash_flow_statement=CashFlowStatement.from_orm(orm_obj.cash_flow_statement),
            price_performance=PricePerformance.from_orm(orm_obj.price_performance),
            created_at=getattr(orm_obj, "created_at", datetime.now()),
            updated_at=getattr(orm_obj, "updated_at", datetime.now()),
        )
