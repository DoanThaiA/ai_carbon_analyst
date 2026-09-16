import asyncio
from sqlalchemy import select, text
from db.session import build_sessionmaker, create_engine
from core.config import Settings
import datetime

async def main():
    settings = Settings.from_env()
    engine = create_engine(settings.database_url)
    session_factory = build_sessionmaker(engine)
    
    async with session_factory() as session:
        # Check articles count group by date
        result = await session.execute(text("SELECT date(crawled_at) as cdate, count(*) FROM articles GROUP BY 1 ORDER BY 1 DESC LIMIT 10"))
        print("Crawled articles by date:")
        for row in result:
            print(row)
            
        # Check recent errors or logs in articles?
        result = await session.execute(text("SELECT id, url, title, crawled_at, is_relevant, published_at FROM articles ORDER BY crawled_at DESC LIMIT 5"))
        print("\nRecent articles:")
        for row in result:
            print(row)

asyncio.run(main())
