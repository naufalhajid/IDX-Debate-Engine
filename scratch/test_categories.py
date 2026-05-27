import asyncio
from services.stockbit_api_client import StockbitApiClient

async def test_category(category_name):
    client = StockbitApiClient()
    url = "https://exodus.stockbit.com/stream/v3/symbol/BBCA"
    payload = {"category": category_name, "last_stream_id": 0, "limit": 10}
    response = client.post(url, payload)
    
    if response and "data" in response:
        stream = response["data"].get("stream", [])
        print(f"Category '{category_name}': SUCCESS, returned {len(stream)} posts.")
        if stream:
            print(f"  First post user: @{stream[0].get('user', {}).get('username')}")
    else:
        print(f"Category '{category_name}': FAILED or empty response.")

async def main():
    categories = [
        "STREAM_CATEGORY_ALL",
        "STREAM_CATEGORY_INFLUENCER",
        "STREAM_CATEGORY_VERIFIED",
        "STREAM_CATEGORY_NEWS",
        "STREAM_CATEGORY_IDEA",
        "STREAM_CATEGORY_REPORTS",
        "STREAM_CATEGORY_CHARTS",
    ]
    for cat in categories:
        await test_category(cat)

if __name__ == "__main__":
    import sys
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    if loop.is_running():
        import nest_asyncio
        nest_asyncio.apply()
    
    loop.run_until_complete(main())
