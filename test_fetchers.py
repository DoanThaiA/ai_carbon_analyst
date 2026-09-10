import asyncio
from crawl_prices.market_events_fetcher import fetch_eia_weekly_inventory, fetch_baker_hughes_rig_count

async def main():
    print("Testing EIA:")
    eia = await fetch_eia_weekly_inventory()
    print(eia)
    
    print("\nTesting Baker Hughes:")
    bh = await fetch_baker_hughes_rig_count()
    print(bh)

asyncio.run(main())
