from schemas.key_analysis import KeyAnalysis
from schemas.stock import Stock


class KeyAnalysisAnalyser:
    """
    Analyzes key financial metrics for a list of stocks and generates an analysis sheet.

    Attributes:
    - stocks : list of Stock
        A list of Stock objects to be analyzed.
    """

    def __init__(self, stocks: [Stock]):
        """
        Initializes the KeyAnalysisAnalyser with a list of stocks.

        Parameters:
        - stocks : list of Stock
            A list of Stock objects to be analyzed.
        """
        self.stocks = stocks
        self._calculate()

    def _calculate(self):
        """
        Calculates various financial metrics for each stock and updates their key analysis.

        This method computes metrics such as normal price, price to equity discount, relative PE ratio,
        EPS growth, debt to total assets ratio, liquidity differential, CCE, operating efficiency,
        dividend payout efficiency, yearly price change, composite rank, and net debt to equity ratio.
        """
        for stock in self.stocks:
            fundamental = stock.fundamental

            if fundamental is None:
                stock.key_analysis = KeyAnalysis()
                continue

            normal_price = (
                fundamental.per_share.current_book_value_per_share
                * fundamental.management_effectiveness.return_on_equity_ttm
                * 10
            )

            if normal_price > 0:
                try:
                    price_to_equity_disct = abs(
                        1 - (stock.stock_price.close / normal_price) * 100
                    )
                except AttributeError as _e:
                    price_to_equity_disct = 0
            else:
                price_to_equity_disct = 0

            if fundamental.current_valuation.ihsg_pe_ratio_ttm_median > 0:
                try:
                    relative_pe_ratio_ttm = (
                        fundamental.market_rank.rank_current_pe_ratio_ttm
                        / fundamental.current_valuation.ihsg_pe_ratio_ttm_median
                    )
                except AttributeError as _e:
                    price_to_equity_disct = 0
            else:
                relative_pe_ratio_ttm = 0

            if fundamental.per_share.current_eps_ttm > 0:
                try:
                    eps_growth = (
                        fundamental.per_share.current_eps_annualised
                        - fundamental.per_share.current_eps_ttm
                    ) / fundamental.per_share.current_eps_ttm
                except AttributeError as _e:
                    eps_growth = 0
            else:
                eps_growth = 0

            if fundamental.balance_sheet.total_assets_quarter > 0:
                try:
                    debt_to_total_assets_ratio = (
                        fundamental.balance_sheet.total_debt_quarter
                        / fundamental.balance_sheet.total_assets_quarter
                    )
                except AttributeError as _e:
                    debt_to_total_assets_ratio = 0
            else:
                debt_to_total_assets_ratio = 0

            if fundamental.solvency.quick_ratio_quarter > 0:
                try:
                    liquidity_differential = (
                        fundamental.solvency.current_ratio_quarter
                        / fundamental.solvency.quick_ratio_quarter
                    )
                except AttributeError as _e:
                    liquidity_differential = 0
            else:
                liquidity_differential = 0

            if fundamental.income_statement.revenue_ttm > 0:
                try:
                    cce = (
                        fundamental.cash_flow_statement.cash_from_operations_ttm
                        / fundamental.income_statement.revenue_ttm
                    )
                except AttributeError as _e:
                    cce = 0
            else:
                cce = 0

            if fundamental.profitability.gross_profit_margin_quarter > 0:
                try:
                    operating_efficiency = (
                        fundamental.profitability.operating_profit_margin_quarter
                        / fundamental.profitability.gross_profit_margin_quarter
                    )
                except AttributeError as _e:
                    operating_efficiency = 0
            else:
                operating_efficiency = 0

            if fundamental.income_statement.net_income_ttm > 0:
                try:
                    dividend_payout_efficiency = (
                        fundamental.dividend.dividend
                        + fundamental.dividend.dividend_ttm
                    ) / (2 * fundamental.income_statement.net_income_ttm)
                except AttributeError as _e:
                    dividend_payout_efficiency = 0
            else:
                dividend_payout_efficiency = 0

            if fundamental.income_statement.revenue_ttm > 0:
                try:
                    yearly_price_change = (
                        fundamental.price_performance.one_year_price_returns
                        / fundamental.income_statement.revenue_ttm
                    )
                except AttributeError as _e:
                    yearly_price_change = 0
            else:
                yearly_price_change = 0

            composite_rank = (
                fundamental.market_rank.rank_current_pe_ratio_ttm
                + fundamental.market_rank.rank_current_pe_ratio_ttm
                + fundamental.market_rank.rank_earnings_yield
                + fundamental.market_rank.rank_p_s
                + fundamental.market_rank.rank_p_b
                + fundamental.market_rank.rank_near_52_weeks_high
            ) / 6

            if fundamental.balance_sheet.total_equity > 0:
                try:
                    net_debt_to_equity = (
                        fundamental.balance_sheet.net_debt_quarter
                        / fundamental.balance_sheet.total_equity
                    )
                except AttributeError as _e:
                    net_debt_to_equity = 0
            else:
                net_debt_to_equity = 0

            stock.key_analysis = KeyAnalysis(
                normal_price=round(normal_price, 2),
                price_to_equity_discount=round(price_to_equity_disct, 2),
                relative_pe_ratio_ttm=round(relative_pe_ratio_ttm, 2),
                eps_growth=round(eps_growth, 2),
                debt_to_total_assets_ratio=round(debt_to_total_assets_ratio, 2),
                liquidity_differential=round(liquidity_differential, 2),
                cce=round(cce, 2),
                operating_efficiency=round(operating_efficiency, 2),
                dividend_payout_efficiency=round(dividend_payout_efficiency, 2),
                yearly_price_change=round(yearly_price_change, 2),
                composite_rank=round(composite_rank, 2),
                net_debt_to_equity_ratio=round(net_debt_to_equity, 2),
            )

    def analysis_sheet(self):
        """
        Generates a sheet of analysis for each stock that derives from fundamental.

        Returns:
        - list of list: A list of rows, each containing key statistics for a stock.
        """
        headers = [
            "Ticker",
            "PBV x ROE",
            "Close Price",
            "Price to Equity Discount (%)",
            "Relative PE ratio (TTM)",
            "EPS Growth",
            "Debt to Total Assets Ratio",
            "Liquidity Differential",
            "CCE",
            "Operating Efficiency",
            "Dividend Payout Efficiency",
            "Yearly Price Change",
            "Composite Rank",
            "Net Debt to Equity",
        ]

        sheet_values = [headers]
        for stock in self.stocks:
            row = [
                stock.ticker,
                getattr(stock.key_analysis, "normal_price", 0),
                getattr(stock.stock_price, "close", 0),
                getattr(stock.key_analysis, "price_to_equity_discount", 0),
                getattr(stock.key_analysis, "relative_pe_ratio_ttm", 0),
                getattr(stock.key_analysis, "eps_growth", 0),
                getattr(stock.key_analysis, "debt_to_total_assets_ratio", 0),
                getattr(stock.key_analysis, "liquidity_differential", 0),
                getattr(stock.key_analysis, "cce", 0),
                getattr(stock.key_analysis, "operating_efficiency", 0),
                getattr(stock.key_analysis, "dividend_payout_efficiency", 0),
                getattr(stock.key_analysis, "yearly_price_change", 0),
                getattr(stock.key_analysis, "composite_rank", 0),
                getattr(stock.key_analysis, "net_debt_to_equity_ratio", 0),
            ]

            sheet_values.append(row)

        return sheet_values
