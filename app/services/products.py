from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, InsufficientStockError, NotFoundError
from app.db.models import Product
from app.schemas import ProductCreate, ProductUpdate


async def create_product(session: AsyncSession, data: ProductCreate) -> Product:
    product = Product(**data.model_dump())
    session.add(product)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("A product with this SKU already exists", sku=data.sku) from exc
    await session.refresh(product)
    return product


async def get_product(session: AsyncSession, product_id: int) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product not found", product_id=product_id)
    return product


async def list_products(session: AsyncSession, *, limit: int, offset: int) -> list[Product]:
    result = await session.scalars(
        select(Product).order_by(Product.id).limit(limit).offset(offset)
    )
    return list(result)


async def update_product(session: AsyncSession, product_id: int, data: ProductUpdate) -> Product:
    product = await get_product(session, product_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(product, field, value)
    await session.commit()
    await session.refresh(product)
    return product


async def adjust_stock(session: AsyncSession, product_id: int, delta: int) -> Product:
    """
    Warehouse stock movement as a single atomic UPDATE.

    ``stock = stock + :delta`` is evaluated by Postgres under the row lock the
    UPDATE takes, so concurrent restocks and orders can't lose each other's
    changes, and the WHERE clause refuses to go below zero.
    """
    row = (
        await session.execute(
            text(
                "UPDATE products SET stock = stock + :delta, updated_at = now() "
                "WHERE id = :id AND stock + :delta >= 0 RETURNING id"
            ),
            {"id": product_id, "delta": delta},
        )
    ).first()
    if row is None:
        await session.rollback()
        product = await get_product(session, product_id)  # raises 404 if missing
        raise InsufficientStockError(
            "Adjustment would make stock negative",
            product_id=product_id, current_stock=product.stock, delta=delta,
        )
    await session.commit()
    return await session.get(Product, product_id, populate_existing=True)
