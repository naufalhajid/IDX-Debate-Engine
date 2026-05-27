import asyncio
import json
from services.stockbit_api_client import StockbitApiClient

async def main():
    client = StockbitApiClient()
    url = "https://exodus.stockbit.com/stream/v3/symbol/BBCA"
    payload = {"category": "STREAM_CATEGORY_ALL", "last_stream_id": 0, "limit": 40}
    response = client.post(url, payload)
    
    if response and "data" in response and "stream" in response["data"]:
        stream = response["data"]["stream"]
        total = len(stream)
        verified_count = sum(1 for post in stream if post.get("user", {}).get("is_verified"))
        pro_count = sum(1 for post in stream if post.get("user", {}).get("is_pro"))
        
        print(f"Total posts retrieved: {total}")
        print(f"Verified posts count: {verified_count}")
        print(f"Pro posts count: {pro_count}")
        
        print("\nVerified Users:")
        for post in stream:
            user = post.get("user", {})
            if user.get("is_verified") or user.get("is_pro"):
                print(f"- @{user.get('username')} | verified={user.get('is_verified')} | pro={user.get('is_pro')}")
                # print(f"  Content: {post.get('content')[:100]}...")
    else:
        print("Response structure unexpected or empty")

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
