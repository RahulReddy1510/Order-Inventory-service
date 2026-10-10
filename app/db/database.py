"""
Async database setup and connection-handling strategy.

The three consumers in the scenario have very different profiles:

* storefront   - many short reads
* warehouse /  - short write transactions that take row locks
  orders
* reporting    - few, slow, heavy aggregate reads

If they all shared one pool, a burst of slow report queries could check out
every connection, and order placement would queue behind them (or time out).
So we run TWO engines, each with its own bounded pool:

  OLTP engine       pool_size=10, max_overflow=10   -> at most 20 conns / process
  Reporting engine  pool_size=3,  max_overflow=0    -> at most  3 conns / process

The reporting pool is the "bulkhead": heavy work can only ever occupy 3
connections per process, no matter how many report requests arrive. Extra
report requests wait for a reporting connection (up to report_pool_timeout)
instead of stealing transactional capacity.

Connection budget (must stay under Postgres max_connections=100):
  WEB_CONCURRENCY(2) * (20 + 3) = 46 connections worst case, leaving headroom
  for psql sessions, migrations, and a rolling restart that briefly doubles
  the number of processes.

Other settings and why:
  pool_timeout      Fail fast (503) rather than letting requests hang forever
                    when the pool is saturated - back-pressure, not a pile-up.
  pool_pre_ping     Detects connections killed by a Postgres restart or idle
                    timeout before we hand them to a request.
  pool_recycle      Periodically replaces long-lived connections.
  statement_timeout Set server-side per engine, so a pathological query is
                    killed by Postgres instead of holding a connection forever.
                    Reporting gets a longer timeout than OLTP.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()


class Base(DeclarativeBase):
    pass


def _make_engine(
    *,
    pool_size: int,
    max_overflow: int,
    pool_timeout: float,
    statement_timeout_ms: int,
    application_name: str,
) -> AsyncEngine:
    return create_async_engine(
        settings.database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            # asyncpg passes these as Postgres session parameters.
            "server_settings": {
                "statement_timeout": str(statement_timeout_ms),
                # Makes each pool identifiable in pg_stat_activity.
                "application_name": application_name,
            },
        },
    )


oltp_engine = _make_engine(
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    statement_timeout_ms=settings.db_statement_timeout_ms,
    application_name="inventory-oltp",
)

reporting_engine = _make_engine(
    pool_size=settings.report_pool_size,
    max_overflow=settings.report_max_overflow,
    pool_timeout=settings.report_pool_timeout,
    statement_timeout_ms=settings.report_statement_timeout_ms,
    application_name="inventory-reporting",
)

# expire_on_commit=False: we return ORM objects after commit without
# triggering implicit (and in async, illegal) lazy refreshes.
OltpSession = async_sessionmaker(oltp_engine, expire_on_commit=False)
ReportingSession = async_sessionmaker(reporting_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Request-scoped session from the transactional pool."""
    async with OltpSession() as session:
        yield session


async def get_reporting_session() -> AsyncIterator[AsyncSession]:
    """
    Request-scoped session from the isolated reporting pool.

    REPEATABLE READ + READ ONLY gives every aggregate in a report the same
    consistent snapshot, and under Postgres MVCC readers never block writers,
    so a long report does not hold up order placement.
    """
    async with ReportingSession() as session:
        await session.connection(
            execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
        )
        yield session


async def init_models() -> None:
    """Create tables on startup (see README: would be Alembic in production)."""
    import app.db.models  # noqa: F401  (register models on Base.metadata)

    async with oltp_engine.begin() as conn:
        # Serialise schema creation across worker processes.
        await conn.exec_driver_sql("SELECT pg_advisory_xact_lock(424242)")
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engines() -> None:
    await oltp_engine.dispose()
    await reporting_engine.dispose()
