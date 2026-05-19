from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import mapped_column, Mapped, relationship

from db.models import BaseModel, FLOAT


class Fundamental(BaseModel):
    __tablename__ = "fundamentals"

    stat_id = mapped_column(ForeignKey("stats.id"))
    stat: Mapped["Stat"] = relationship(back_populates="fundamental")

    current_valuation_id = mapped_column(ForeignKey("current_valuations.id"))
    current_valuation: Mapped["CurrentValuation"] = relationship(
        back_populates="fundamental"
    )

    per_share_id = mapped_column(ForeignKey("per_shares.id"))
    per_share: Mapped["PerShare"] = relationship(back_populates="fundamental")

    solvency_id = mapped_column(ForeignKey("solvencies.id"))
    solvency: Mapped["Solvency"] = relationship(back_populates="fundamental")

    management_effectiveness_id = mapped_column(
        ForeignKey("management_effectivenesses.id")
    )
    management_effectiveness: Mapped["ManagementEffectiveness"] = relationship(
        back_populates="fundamental"
    )

    profitability_id = mapped_column(ForeignKey("profitabilities.id"))
    profitability: Mapped["Profitability"] = relationship(back_populates="fundamental")

    growth_id = mapped_column(ForeignKey("growths.id"))
    growth: Mapped["Growth"] = relationship(back_populates="fundamental")

    dividend_id = mapped_column(ForeignKey("dividends.id"))
    dividend: Mapped["Dividend"] = relationship(back_populates="fundamental")

    market_rank_id = mapped_column(ForeignKey("market_ranks.id"))
    market_rank: Mapped["MarketRank"] = relationship(back_populates="fundamental")

    income_statement_id = mapped_column(ForeignKey("income_statements.id"))
    income_statement: Mapped["IncomeStatement"] = relationship(
        back_populates="fundamental"
    )

    balance_sheet_id = mapped_column(ForeignKey("balance_sheets.id"))
    balance_sheet: Mapped["BalanceSheet"] = relationship(back_populates="fundamental")

    cash_flow_statement_id = mapped_column(ForeignKey("cash_flow_statements.id"))
    cash_flow_statement: Mapped["CashFlowStatement"] = relationship(
        back_populates="fundamental"
    )

    price_performance_id = mapped_column(ForeignKey("price_performances.id"))
    price_performance: Mapped["PricePerformance"] = relationship(
        back_populates="fundamental"
    )

    stock_ticker = mapped_column(ForeignKey("stocks.ticker"))
    stock: Mapped["Stock"] = relationship(back_populates="fundamentals")


class CurrentValuation(BaseModel):
    __tablename__ = "current_valuations"

    current_pe_ratio_annual: Mapped[FLOAT]
    current_pe_ratio_ttm: Mapped[FLOAT]
    forward_pe_ratio: Mapped[FLOAT]
    ihsg_pe_ratio_ttm_median: Mapped[FLOAT]
    earnings_yield_ttm: Mapped[FLOAT]
    current_price_to_sales_ttm: Mapped[FLOAT]
    current_price_to_book_value: Mapped[FLOAT]
    current_price_to_cashflow_ttm: Mapped[FLOAT]
    current_price_to_free_cashflow_ttm: Mapped[FLOAT]
    ev_to_ebit_ttm: Mapped[FLOAT]
    ev_to_ebitda_ttm: Mapped[FLOAT]
    peg_ratio: Mapped[FLOAT]
    peg_ratio_3yr: Mapped[FLOAT]
    peg_forward: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(
        back_populates="current_valuation"
    )


class PerShare(BaseModel):
    __tablename__ = "per_shares"

    current_eps_ttm: Mapped[FLOAT]
    current_eps_annualised: Mapped[FLOAT]
    revenue_per_share_ttm: Mapped[FLOAT]
    cash_per_share_quarter: Mapped[FLOAT]
    current_book_value_per_share: Mapped[FLOAT]
    free_cashflow_per_share_ttm: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="per_share")


class Solvency(BaseModel):
    __tablename__ = "solvencies"

    current_ratio_quarter: Mapped[FLOAT]
    quick_ratio_quarter: Mapped[FLOAT]
    debt_to_equity_ratio_quarter: Mapped[FLOAT]
    lt_debt_equity_quarter: Mapped[FLOAT]
    total_liabilities_equity_quarter: Mapped[FLOAT]
    total_debt_total_assets_quarter: Mapped[FLOAT]
    financial_leverage_quarter: Mapped[FLOAT]
    interest_rate_coverage_ttm: Mapped[FLOAT]
    free_cash_flow_quarter: Mapped[FLOAT]
    altman_z_score_modified: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="solvency")


class ManagementEffectiveness(BaseModel):
    __tablename__ = "management_effectivenesses"

    return_on_assets_ttm: Mapped[FLOAT]
    return_on_equity_ttm: Mapped[FLOAT]
    return_on_capital_employed_ttm: Mapped[FLOAT]
    return_on_invested_capital_ttm: Mapped[FLOAT]
    days_sales_outstanding_quarter: Mapped[FLOAT]
    days_inventory_quarter: Mapped[FLOAT]
    days_payables_outstanding_quarter: Mapped[FLOAT]
    cash_conversion_cycle_quarter: Mapped[FLOAT]
    receivables_turnover_quarter: Mapped[FLOAT]
    asset_turnover_ttm: Mapped[FLOAT]
    inventory_turnover_ttm: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(
        back_populates="management_effectiveness"
    )


class Profitability(BaseModel):
    __tablename__ = "profitabilities"

    gross_profit_margin_quarter: Mapped[FLOAT]
    operating_profit_margin_quarter: Mapped[FLOAT]
    net_profit_margin_quarter: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="profitability")


class Growth(BaseModel):
    __tablename__ = "growths"

    revenue_quarter_yoy_growth: Mapped[FLOAT]
    gross_profit_quarter_yoy_growth: Mapped[FLOAT]
    net_income_quarter_yoy_growth: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="growth")


class Dividend(BaseModel):
    __tablename__ = "dividends"

    dividend: Mapped[FLOAT]
    dividend_ttm: Mapped[FLOAT]
    payout_ratio: Mapped[FLOAT]
    dividend_yield: Mapped[FLOAT]
    latest_dividend_ex_date = mapped_column(String, default="")

    fundamental: Mapped["Fundamental"] = relationship(back_populates="dividend")


class MarketRank(BaseModel):
    __tablename__ = "market_ranks"

    piotroski_f_score: Mapped[FLOAT]
    eps_rating: Mapped[FLOAT]
    relative_strength_rating: Mapped[FLOAT]
    rank_market_cap: Mapped[FLOAT]
    rank_current_pe_ratio_ttm: Mapped[FLOAT]
    rank_earnings_yield: Mapped[FLOAT]
    rank_p_s: Mapped[FLOAT]
    rank_p_b: Mapped[FLOAT]
    rank_near_52_weeks_high: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="market_rank")


class IncomeStatement(BaseModel):
    __tablename__ = "income_statements"

    revenue_ttm: Mapped[FLOAT]
    gross_profit_ttm: Mapped[FLOAT]
    ebitda_ttm: Mapped[FLOAT]
    net_income_ttm: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="income_statement")


class BalanceSheet(BaseModel):
    __tablename__ = "balance_sheets"

    cash_quarter: Mapped[FLOAT]
    total_assets_quarter: Mapped[FLOAT]
    total_liabilities_quarter: Mapped[FLOAT]
    working_capital_quarter: Mapped[FLOAT]
    total_equity: Mapped[FLOAT]
    long_term_debt_quarter: Mapped[FLOAT]
    short_term_debt_quarter: Mapped[FLOAT]
    total_debt_quarter: Mapped[FLOAT]
    net_debt_quarter: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="balance_sheet")


class CashFlowStatement(BaseModel):
    __tablename__ = "cash_flow_statements"

    cash_from_operations_ttm: Mapped[FLOAT]
    cash_from_investing_ttm: Mapped[FLOAT]
    cash_from_financing_ttm: Mapped[FLOAT]
    capital_expenditure_ttm: Mapped[FLOAT]
    free_cash_flow_ttm: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(
        back_populates="cash_flow_statement"
    )


class PricePerformance(BaseModel):
    __tablename__ = "price_performances"

    one_week_price_returns: Mapped[FLOAT]
    three_month_price_returns: Mapped[FLOAT]
    one_month_price_returns: Mapped[FLOAT]
    six_month_price_returns: Mapped[FLOAT]
    one_year_price_returns: Mapped[FLOAT]
    three_year_price_returns: Mapped[FLOAT]
    five_year_price_returns: Mapped[FLOAT]
    ten_year_price_returns: Mapped[FLOAT]
    year_to_date_price_returns: Mapped[FLOAT]
    fifty_two_week_high: Mapped[FLOAT]
    fifty_two_week_low: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(
        back_populates="price_performance"
    )


class Stat(BaseModel):
    __tablename__ = "stats"

    current_share_outstanding: Mapped[FLOAT]
    market_cap: Mapped[FLOAT]
    enterprise_value: Mapped[FLOAT]

    fundamental: Mapped["Fundamental"] = relationship(back_populates="stat")
