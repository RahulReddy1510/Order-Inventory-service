"""
Order placement - the race-sensitive path.

Correctness under concurrency comes from pessimistic row locking:

1. ``SELECT ... FROM products WHERE id IN (...) ORDER BY id FOR UPDATE``
   locks every product in the order. A second transaction ordering the same
   product blocks at this line until the first commits, then re-reads the
   *committed* stock. So the "check stock, then decrement" sequence can never
   interleave between two orders.
2. Rows are locked in ascending id order. Two multi-product orders that share
   products therefore always acquire locks in the same order, which rules out
   deadlocks (A holds 1 waits 2 / B holds 2 waits 1).
3. ``SET LOCAL lock_timeout`` bounds how long we wait for a hot row. Under
   extreme contention a request fails fast with 503 + Retry-After instead of
   holding a pooled connection indefinitely.
4. All-or-nothing: if any line lacks stock, nothing is decremented and the
   transaction rolls back (409).
5. ``CHECK (stock >= 0)`` on the table is a final guard if any of the above
   were ever bypassed.
"""

from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import InsufficientStockError, NotFoundError
from app.db.models import Order, OrderItem, OrderStatus, Product
from app.schemas import OrderCreate

settings = get_settings()


async def place_order(session: AsyncSession, data: OrderCreate) -> tuple[Order, dict[int, int]]:
    """Returns the created order and the new stock level per product."""
    wanted = {item.product_id: item.quantity for item in data.items}

    async with session.begin():
        await session.execute(
            text(f"SET LOCAL lock_timeout = '{int(settings.order_lock_timeout_ms)}ms'")
        )
        products = (
            await session.scalars(
                select(Product)
                .where(Product.id.in_(wanted))
                .order_by(Product.id)
                .with_for_update()
            )
        ).all()

        found = {p.id for p in products}
        missing = sorted(set(wanted) - found)
        if missing:
            raise NotFoundError("Product(s) not found", product_ids=missing)

        shortages = [
            {"product_id": p.id, "requested": wanted[p.id], "available": p.stock}
            for p in products
            if p.stock < wanted[p.id]
        ]
        if shortages:
            raise InsufficientStockError("Insufficient stock", shortages=shortages)

        total = Decimal("0")
        items: list[OrderItem] = []
        for p in products:
            qty = wanted[p.id]
            p.stock -= qty
            total += p.price * qty
            items.append(OrderItem(product_id=p.id, quantity=qty, unit_price=p.price))

        order = Order(status=OrderStatus.CONFIRMED, total_amount=total, items=items)
        session.add(order)
        await session.flush()  # assigns order.id / created_at inside the txn
        await session.refresh(order, ["created_at"])
    # Commit has happened here; row locks are released.
    return order, {p.id: p.stock for p in products}


async def get_order(session: AsyncSession, order_id: int) -> Order:
    order = await session.get(Order, order_id)
    if order is None:
        raise NotFoundError("Order not found", order_id=order_id)
    return order
