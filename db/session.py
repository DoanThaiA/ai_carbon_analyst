
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

DEFAULT_POOL_SIZE = 5
DEFAULT_MAX_OVERFLOW = 5
DEFAULT_POOL_RECYCLE_SECONDS = 1800  # tránh dùng connection đã bị firewall/proxy cắt sau thời gian dài idle


def to_asyncpg_dsn(dsn: str) -> str:
    """DATABASE_URL trong .env dùng dạng "postgresql://..." (asyncpg trần
    trước đây) — chuẩn hoá về driver asyncpg của SQLAlchemy nếu chưa có,
    để không phải sửa .env khi migrate sang SQLAlchemy."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+asyncpg://" + dsn[len("postgresql://"):]
    return dsn


def create_engine(
    dsn: str,
    *,
    pool_size: int = DEFAULT_POOL_SIZE,
    max_overflow: int = DEFAULT_MAX_OVERFLOW,
) -> AsyncEngine:
    engine = create_async_engine(
        to_asyncpg_dsn(dsn),
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,  # phát hiện connection chết (DB restart/timeout) trước khi dùng, thay vì fail giữa transaction
        pool_recycle=DEFAULT_POOL_RECYCLE_SECONDS,
    )
    return engine

def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
