from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_reporting_session
from app.schemas import SalesReport
from app.services import reports as svc

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/sales", response_model=SalesReport)
async def sales_report(
    session: Annotated[AsyncSession, Depends(get_reporting_session)],
    since_days: Annotated[int | None, Query(ge=1, le=3650)] = None,
    top_n: Annotated[int, Query(ge=1, le=100)] = 10,
    low_stock_threshold: Annotated[int, Query(ge=0)] = 5,
    simulate_slow_ms: Annotated[
        int, Query(ge=0, le=10_000, description="Testing aid: hold the query open this long")
    ] = 0,
):
    """
    Heavy aggregate across all products and orders (the 'reporting job').
    Uses the isolated reporting connection pool.
    """
    return await svc.sales_report(
        session,
        since_days=since_days,
        top_n=top_n,
        low_stock_threshold=low_stock_threshold,
        simulate_slow_ms=simulate_slow_ms,
    )
