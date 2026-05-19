from datetime import datetime

from schemas import BaseDataClass


class StockPrice(BaseDataClass):
    price: float = 0.0
    volume: int = 0
    change: float = 0.0
    percentage_change: float = 0.0
    average: float = 0.0
    close: float = 0.0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    ara: float = 0.0
    arb: float = 0.0
    frequency: float = 0.0
    fsell: float = 0.0
    fbuy: float = 0.0
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
