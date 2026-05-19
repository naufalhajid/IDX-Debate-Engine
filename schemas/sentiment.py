from datetime import datetime
from typing import Optional

from schemas import BaseDataClass


class Sentiment(BaseDataClass):
    content: str = ""
    rate: float = 0.0
    category: str = ""
    posted_at: Optional[datetime] = None
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
