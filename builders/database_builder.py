from sqlalchemy import select

from builders.builder_interface import BuilderInterface
from db import (
    StockPrice,
    Sentiment,
    KeyAnalysis,
    CurrentValuation,
    PerShare,
    Solvency,
    ManagementEffectiveness,
    Profitability,
    Growth,
    Dividend,
    MarketRank,
    IncomeStatement,
    BalanceSheet,
    CashFlowStatement,
    PricePerformance,
    Stat,
    Fundamental,
)
from db.models.stock import Stock

from db.session import get_session
from schemas.stock import Stock as StockSchema
from utils.logger_config import logger


class DatabaseBuilder(BuilderInterface):
    def __init__(self, stocks=[StockSchema]):
        logger.info("Database Builder started")
        self.stocks = stocks

    def insert_stock(self):
        logger.info("Storing stocks")
        for stock in self.stocks:
            with get_session() as session:
                stock_model = Stock(
                    ticker=stock.ticker,
                    name=stock.name,
                    ipo_date=stock.ipo_date,
                    note=stock.note,
                    market_cap=stock.market_cap,
                    home_page=stock.home_page,
                )

                session.add(stock_model)

    def update_or_insert_stock(self):
        logger.info("Updating or inserting stocks")
        for stock in self.stocks:
            with get_session() as session:
                query = select(Stock).where(Stock.ticker == stock.ticker)

                existing_stock = session.scalars(query).one_or_none()

                if existing_stock:
                    existing_stock.market_cap = stock.market_cap
                    existing_stock.note = stock.note
                    existing_stock.home_page = stock.home_page
                else:
                    stock_model = Stock(
                        ticker=stock.ticker,
                        name=stock.name,
                        ipo_date=stock.ipo_date,
                        note=stock.note,
                        market_cap=stock.market_cap,
                        home_page=stock.home_page,
                    )

                    session.add(stock_model)

    def insert_key_statistic(self):
        logger.info("Storing key statistics")
        for stock in self.stocks:
            with get_session() as session:
                # Create instances of related models
                current_valuation = CurrentValuation(
                    **stock.fundamental.current_valuation.to_dict()
                )
                per_share = PerShare(**stock.fundamental.per_share.to_dict())
                solvency = Solvency(**stock.fundamental.solvency.to_dict())
                management_effectiveness = ManagementEffectiveness(
                    **stock.fundamental.management_effectiveness.to_dict()
                )
                profitability = Profitability(
                    **stock.fundamental.profitability.to_dict()
                )
                growth = Growth(**stock.fundamental.growth.to_dict())
                dividend = Dividend(**stock.fundamental.dividend.to_dict())
                market_rank = MarketRank(**stock.fundamental.market_rank.to_dict())
                income_statement = IncomeStatement(
                    **stock.fundamental.income_statement.to_dict()
                )
                balance_sheet = BalanceSheet(
                    **stock.fundamental.balance_sheet.to_dict()
                )
                cash_flow_statement = CashFlowStatement(
                    **stock.fundamental.cash_flow_statement.to_dict()
                )
                price_performance = PricePerformance(
                    **stock.fundamental.price_performance.to_dict()
                )
                stat = Stat(**stock.fundamental.stat.to_dict())

                # Add all instances to the session
                session.add_all(
                    [
                        current_valuation,
                        per_share,
                        solvency,
                        management_effectiveness,
                        profitability,
                        growth,
                        dividend,
                        market_rank,
                        income_statement,
                        balance_sheet,
                        cash_flow_statement,
                        price_performance,
                        stat,
                    ]
                )

                session.commit()

                # Create the Fundamental instance
                fundamental = Fundamental(
                    stat_id=stat.id,
                    current_valuation_id=current_valuation.id,
                    per_share_id=per_share.id,
                    solvency_id=solvency.id,
                    management_effectiveness_id=management_effectiveness.id,
                    profitability_id=profitability.id,
                    growth_id=growth.id,
                    dividend_id=dividend.id,
                    market_rank_id=market_rank.id,
                    income_statement_id=income_statement.id,
                    balance_sheet_id=balance_sheet.id,
                    cash_flow_statement_id=cash_flow_statement.id,
                    price_performance_id=price_performance.id,
                    stock_ticker=stock.ticker,
                )

                # Add the Fundamental instance to the session
                session.add(fundamental)

    def insert_key_analysis(self):
        logger.info("Storing Key Analysis")
        for stock in self.stocks:
            with get_session() as session:
                key_analysis = KeyAnalysis(
                    normal_price=stock.key_analysis.normal_price,
                    price_to_equity_discount=stock.key_analysis.price_to_equity_discount,
                    relative_pe_ratio_ttm=stock.key_analysis.relative_pe_ratio_ttm,
                    eps_growth=stock.key_analysis.eps_growth,
                    debt_to_total_assets_ratio=stock.key_analysis.debt_to_total_assets_ratio,
                    liquidity_differential=stock.key_analysis.liquidity_differential,
                    cce=stock.key_analysis.cce,
                    operating_efficiency=stock.key_analysis.operating_efficiency,
                    dividend_payout_efficiency=stock.key_analysis.dividend_payout_efficiency,
                    yearly_price_change=stock.key_analysis.yearly_price_change,
                    composite_rank=stock.key_analysis.composite_rank,
                    stock_ticker=stock.ticker,
                )

                session.add(key_analysis)

    def insert_sentiment(self):
        logger.info("Storing Sentiment")
        for stock in self.stocks:
            for sentiment in stock.sentiment:
                with get_session() as session:
                    sentiment = Sentiment(
                        content=sentiment.content,
                        rate=sentiment.rate,
                        stock_ticker=stock.ticker,
                        posted_at=sentiment.posted_at,
                    )
                    session.add(sentiment)

    def insert_stock_price(self):
        logger.info("Storing Stock Price")
        for stock in self.stocks:
            with get_session() as session:
                stock_price = StockPrice(
                    stock_ticker=stock.ticker,
                    price=stock.stock_price.price,
                    volume=stock.stock_price.volume,
                    percentage_change=stock.stock_price.percentage_change,
                    average=stock.stock_price.average,
                    close=stock.stock_price.close,
                    high=stock.stock_price.high,
                    low=stock.stock_price.low,
                    open=stock.stock_price.open,
                    ara=stock.stock_price.ara,
                    arb=stock.stock_price.arb,
                    frequency=stock.stock_price.frequency,
                    fsell=stock.stock_price.fsell,
                    fbuy=stock.stock_price.fbuy,
                )

                session.add(stock_price)
