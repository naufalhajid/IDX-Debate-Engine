from utils.logger_config import logger
from utils.market_data_cache import derive_current_price, prefetch_market_data

async def fetch_current_price(ticker: str) -> float:
    """Fetch last close price for an IHSG (.JK) ticker."""
    try:
        price = derive_current_price(await prefetch_market_data(ticker))
        if price <= 0:
            logger.warning(f"[PriceFetch] {ticker}: no valid price in market data cache")
            return 0.0
        logger.info(f"[PriceFetch] {ticker} -> Rp {price:,.0f}")
        return price
        
    except Exception as e:
        logger.error(f"[PriceFetch] Exception for {ticker}: {e}", exc_info=True)
    
    return 0.0
