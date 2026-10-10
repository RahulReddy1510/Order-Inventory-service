from fastapi import APIRouter
from sqlalchemy import text

from app.db.database import oltp_engine, reporting_engine
from app.services import search

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health():
    checks: dict[str, str] = {}
    try:
        async with oltp_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["postgres"] = f"error: {exc.__class__.__name__}"
    try:
        await search.get_client().ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"
    checks["status"] = "ok" if all(v == "ok" for v in checks.values()) else "degraded"
    return checks


@router.get("/admin/pool-stats")
async def pool_stats():
    """Live view of both connection pools in this worker process."""
    return {
        "oltp": oltp_engine.pool.status(),
        "reporting": reporting_engine.pool.status(),
    }


@router.post("/admin/search/reindex")
async def reindex():
    """Rebuild the search index from Postgres (repairs any drift)."""
    return {"indexed": await search.reindex_all()}
