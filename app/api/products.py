from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_session
from app.schemas import (
    ProductCreate,
    ProductRead,
    ProductUpdate,
    SearchHit,
    SearchResult,
    StockAdjustment,
)
from app.services import products as svc
from app.services import search

router = APIRouter(prefix="/products", tags=["products"])
Session = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(data: ProductCreate, session: Session, bg: BackgroundTasks):
    product = await svc.create_product(session, data)
    bg.add_task(search.index_product, product)  # after the response is sent
    return product


@router.get("", response_model=list[ProductRead])
async def list_products(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    return await svc.list_products(session, limit=limit, offset=offset)


# Declared before /{product_id} so "search" isn't parsed as an id.
@router.get("/search", response_model=SearchResult)
async def search_products(
    q: Annotated[str, Query(max_length=200, description="Words to match in name/description")] = "",
    category: str | None = None,
    min_price: Annotated[float | None, Query(ge=0)] = None,
    max_price: Annotated[float | None, Query(ge=0)] = None,
    in_stock: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """Served entirely from the RediSearch index - no Postgres query."""
    total, hits = await search.search_products(
        q, category=category, min_price=min_price, max_price=max_price,
        in_stock=in_stock, limit=limit, offset=offset,
    )
    return SearchResult(total=total, items=[SearchHit(**h) for h in hits])


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(product_id: int, session: Session):
    return await svc.get_product(session, product_id)


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: int, data: ProductUpdate, session: Session, bg: BackgroundTasks
):
    product = await svc.update_product(session, product_id, data)
    bg.add_task(search.index_product, product)
    return product


@router.post("/{product_id}/stock", response_model=ProductRead)
async def adjust_stock(
    product_id: int, data: StockAdjustment, session: Session, bg: BackgroundTasks
):
    """Warehouse app: restock (positive delta) or write off (negative delta)."""
    product = await svc.adjust_stock(session, product_id, data.delta)
    bg.add_task(search.update_stock, {product.id: product.stock})
    return product
