from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.schemas import OrderCreate, OrderRead
from app.services import orders as svc
from app.services import search

router = APIRouter(prefix="/orders", tags=["orders"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post(
    "",
    response_model=OrderRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "A product in the order does not exist"},
        409: {"description": "Insufficient stock for one or more items"},
        503: {"description": "Row lock or connection pool timeout - retry"},
    },
)
async def place_order(data: OrderCreate, session: Session, bg: BackgroundTasks):
    order, new_stock = await svc.place_order(session, data)
    bg.add_task(search.update_stock, new_stock)
    return order


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(order_id: int, session: Session):
    return await svc.get_order(session, order_id)
