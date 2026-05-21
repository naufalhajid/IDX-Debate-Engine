from threading import Lock

from cachetools import TTLCache


_batch_cache: TTLCache = TTLCache(maxsize=10, ttl=60)
_stocks_cache: TTLCache = TTLCache(maxsize=1, ttl=300)
_cache_lock = Lock()


def get_batch_cache() -> TTLCache:
    return _batch_cache


def get_stocks_cache() -> TTLCache:
    return _stocks_cache


def get_cache_lock() -> Lock:
    return _cache_lock
