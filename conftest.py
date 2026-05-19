from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import uuid4

import pytest

from db.models.fundamental import (
    BalanceSheet,
    CashFlowStatement,
    CurrentValuation,
    Dividend,
    Fundamental as FundamentalModel,
    Growth,
    IncomeStatement,
    ManagementEffectiveness,
    MarketRank,
    PerShare,
    PricePerformance,
    Profitability,
    Solvency,
    Stat,
)
from db.models.key_analysis import KeyAnalysis as KeyAnalysisModel
from db.models.sentiment import Sentiment as SentimentModel
from db.models.stock import Stock as StockModel
from db.models.stock_price import StockPrice as StockPriceModel


@pytest.fixture
def stock_factory() -> Callable[..., StockModel]:
    def _create_stock(
        ticker: Optional[str] = None,
        name: str = "Test Company",
        ipo_date: str = "2020-01-01",
        note: str = "",
        market_cap: float = 100.0,
        home_page: str = "https://example.com",
        with_relations: bool = True,
    ) -> StockModel:
        stock = StockModel(
            ticker=ticker or f"TEST_{uuid4().hex[:8]}",
            name=name,
            ipo_date=ipo_date,
            note=note,
            market_cap=market_cap,
            home_page=home_page,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

        if with_relations:
            stock_price = StockPriceModel(
                volume=1_000_000,
                price=100.0,
                change=0.0,
                percentage_change=0.0,
                average=100.0,
                close=100.0,
                high=101.0,
                low=99.0,
                open=100.0,
                ara=0.0,
                arb=0.0,
                frequency=1.0,
                fsell=0.0,
                fbuy=0.0,
                stock_ticker=stock.ticker,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            fundamental = FundamentalModel(
                stat=Stat(
                    current_share_outstanding=1_000_000.0,
                    market_cap=1_000_000_000.0,
                    enterprise_value=1_100_000_000.0,
                ),
                current_valuation=CurrentValuation(
                    current_pe_ratio_annual=10.0,
                    current_pe_ratio_ttm=12.0,
                    forward_pe_ratio=11.0,
                    ihsg_pe_ratio_ttm_median=15.0,
                    earnings_yield_ttm=0.08,
                    current_price_to_sales_ttm=2.0,
                    current_price_to_book_value=1.5,
                    current_price_to_cashflow_ttm=8.0,
                    current_price_to_free_cashflow_ttm=10.0,
                    ev_to_ebit_ttm=9.0,
                    ev_to_ebitda_ttm=8.0,
                    peg_ratio=1.0,
                    peg_ratio_3yr=1.1,
                    peg_forward=0.9,
                ),
                per_share=PerShare(
                    current_eps_ttm=10.0,
                    current_eps_annualised=12.0,
                    revenue_per_share_ttm=50.0,
                    cash_per_share_quarter=5.0,
                    current_book_value_per_share=20.0,
                    free_cashflow_per_share_ttm=4.0,
                ),
                solvency=Solvency(
                    current_ratio_quarter=2.0,
                    quick_ratio_quarter=1.5,
                    debt_to_equity_ratio_quarter=0.5,
                    lt_debt_equity_quarter=0.3,
                    total_liabilities_equity_quarter=1.2,
                    total_debt_total_assets_quarter=0.4,
                    financial_leverage_quarter=1.8,
                    interest_rate_coverage_ttm=4.0,
                    free_cash_flow_quarter=10_000_000.0,
                    altman_z_score_modified=3.0,
                ),
                management_effectiveness=ManagementEffectiveness(
                    return_on_assets_ttm=0.1,
                    return_on_equity_ttm=0.2,
                    return_on_capital_employed_ttm=0.15,
                    return_on_invested_capital_ttm=0.18,
                    days_sales_outstanding_quarter=30.0,
                    days_inventory_quarter=40.0,
                    days_payables_outstanding_quarter=25.0,
                    cash_conversion_cycle_quarter=45.0,
                    receivables_turnover_quarter=6.0,
                    asset_turnover_ttm=1.2,
                    inventory_turnover_ttm=5.0,
                ),
                profitability=Profitability(
                    gross_profit_margin_quarter=0.4,
                    operating_profit_margin_quarter=0.2,
                    net_profit_margin_quarter=0.15,
                ),
                growth=Growth(
                    revenue_quarter_yoy_growth=0.1,
                    gross_profit_quarter_yoy_growth=0.12,
                    net_income_quarter_yoy_growth=0.11,
                ),
                dividend=Dividend(
                    dividend=1.0,
                    dividend_ttm=1.5,
                    payout_ratio=0.4,
                    dividend_yield=0.03,
                    latest_dividend_ex_date="2024-01-01",
                ),
                market_rank=MarketRank(
                    piotroski_f_score=8.0,
                    eps_rating=90.0,
                    relative_strength_rating=85.0,
                    rank_market_cap=10.0,
                    rank_current_pe_ratio_ttm=15.0,
                    rank_earnings_yield=12.0,
                    rank_p_s=20.0,
                    rank_p_b=18.0,
                    rank_near_52_weeks_high=5.0,
                ),
                income_statement=IncomeStatement(
                    revenue_ttm=1_000_000_000.0,
                    gross_profit_ttm=400_000_000.0,
                    ebitda_ttm=250_000_000.0,
                    net_income_ttm=150_000_000.0,
                ),
                balance_sheet=BalanceSheet(
                    cash_quarter=100_000_000.0,
                    total_assets_quarter=2_000_000_000.0,
                    total_liabilities_quarter=800_000_000.0,
                    working_capital_quarter=200_000_000.0,
                    total_equity=1_200_000_000.0,
                    long_term_debt_quarter=300_000_000.0,
                    short_term_debt_quarter=200_000_000.0,
                    total_debt_quarter=500_000_000.0,
                    net_debt_quarter=400_000_000.0,
                ),
                cash_flow_statement=CashFlowStatement(
                    cash_from_operations_ttm=300_000_000.0,
                    cash_from_investing_ttm=-50_000_000.0,
                    cash_from_financing_ttm=20_000_000.0,
                    capital_expenditure_ttm=80_000_000.0,
                    free_cash_flow_ttm=220_000_000.0,
                ),
                price_performance=PricePerformance(
                    one_week_price_returns=0.02,
                    three_month_price_returns=0.05,
                    one_month_price_returns=0.03,
                    six_month_price_returns=0.1,
                    one_year_price_returns=0.25,
                    three_year_price_returns=0.5,
                    five_year_price_returns=0.8,
                    ten_year_price_returns=1.5,
                    year_to_date_price_returns=0.12,
                    fifty_two_week_high=120.0,
                    fifty_two_week_low=80.0,
                ),
                stock_ticker=stock.ticker,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            sentiment = SentimentModel(
                content="good",
                rate=1.0,
                category="positive",
                posted_at=datetime.now(timezone.utc),
                stock_ticker=stock.ticker,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            key_analysis = KeyAnalysisModel(
                normal_price=100.0,
                price_to_equity_discount=0.0,
                relative_pe_ratio_ttm=1.0,
                eps_growth=0.1,
                debt_to_total_assets_ratio=0.2,
                liquidity_differential=1.0,
                cce=0.0,
                operating_efficiency=1.0,
                dividend_payout_efficiency=0.3,
                yearly_price_change=0.0,
                composite_rank=1.0,
                net_debt_to_equity_ratio=0.0,
                stock_ticker=stock.ticker,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            stock.stock_prices = [stock_price]
            stock.fundamentals = [fundamental]
            stock.sentiments = [sentiment]
            stock.key_analyses = [key_analysis]

        return stock

    return _create_stock
