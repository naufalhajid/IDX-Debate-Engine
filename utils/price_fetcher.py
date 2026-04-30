import asyncio
import yfinance as yf
import pandas as pd
from utils.logger_config import logger

async def fetch_current_price(ticker: str) -> float:
    """Fetch last close price for an IHSG (.JK) ticker."""
    try:
        # Download data
        data = await asyncio.to_thread(
            yf.download, f"{ticker}.JK", period="5d", progress=False
        )

        # Cek jika data kosong
        if data is None or len(data) == 0:
            logger.warning(f"[PriceFetch] {ticker}: empty response from yfinance")
            return 0.0

        # FIX UTAMA: Flatten MultiIndex columns (khusus yfinance v1.3+)
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = data.columns.get_level_values(0)
        
        # Pastikan kolom 'Close' ada setelah flattening
        if 'Close' not in data.columns:
            logger.warning(f"[PriceFetch] {ticker}: 'Close' column not found after flattening")
            return 0.0

        # Ambil harga terakhir non-NaN (skip rows dengan NaN di market belum tutup)
        close_prices = data["Close"].dropna()
        if len(close_prices) == 0:
            logger.warning(f"[PriceFetch] {ticker}: No valid close prices found")
            return 0.0
        
        last_close = close_prices.iloc[-1]
            
        price = float(last_close)
        logger.info(f"[PriceFetch] {ticker} -> Rp {price:,.0f}")
        return price
        
    except Exception as e:
        logger.error(f"[PriceFetch] Exception for {ticker}: {e}", exc_info=True)
    
    return 0.0