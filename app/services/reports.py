"""
Reporting aggregates.

Runs on the reporting engine (separate, small pool) inside a single
REPEATABLE READ READ ONLY transaction, so all the numbers in one report come
from the same snapshot even while orders keep committing.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas import CategorySummary, ProductSalesRow, SalesReport

# Per-product sales joined with current stock. Used as a CTE by every query below.
_PRODUCT_SALES = """
WITH sales AS (
    SELECT oi.product_id,
           SUM(oi.quantity)                  AS units_sold,
           SUM(oi.quantity * oi.unit_price)  AS revenue,
           COUNT(DISTINCT oi.order_id)       AS order_count
    FROM order_items oi
    JOIN orders o ON o.id = oi.order_id
    WHERE o.status = 'confirmed' AND o.created_at >= :since
    GROUP BY oi.product_id
),
product_sales AS (
    SELECT p.id AS product_id, p.sku, p.name, p.category,
           COALESCE(s.units_sold, 0)  AS units_sold,
           COALESCE(s.revenue, 0)     AS revenue,
           COALESCE(s.order_count, 0) AS order_count,
           p.stock                    AS current_stock,
           p.stock * p.price          AS stock_value,
           CASE WHEN COALESCE(s.units_sold, 0) + p.stock = 0 THEN 0
                ELSE ROUND(100.0 * COALESCE(s.units_sold, 0)
                           / (COALESCE(s.units_sold, 0) + p.stock), 2)
           END                        AS sell_through_pct
    FROM products p
    LEFT JOIN sales s ON s.product_id = p.id
)
"""


async def sales_report(
    session: AsyncSession,
    *,
    since_days: int | None,
    top_n: int,
    low_stock_threshold: int,
    simulate_slow_ms: int = 0,
) -> SalesReport:
    since = (
        datetime.now(UTC) - timedelta(days=since_days)
        if since_days is not None
        else datetime(1970, 1, 1, tzinfo=UTC)
    )
    params = {"since": since, "top_n": top_n, "low": low_stock_threshold}

    if simulate_slow_ms:
        # Load-test knob: makes the report hold its reporting connection for a
        # while, to demonstrate that slow reports don't stall order placement.
        await session.execute(text("SELECT pg_sleep(:s)"), {"s": simulate_slow_ms / 1000})

    totals = (
        await session.execute(
            text(
                """
                SELECT
                  (SELECT COUNT(*) FROM orders
                     WHERE status = 'confirmed' AND created_at >= :since)        AS total_orders,
                  (SELECT COALESCE(SUM(total_amount), 0) FROM orders
                     WHERE status = 'confirmed' AND created_at >= :since)        AS total_revenue,
                  (SELECT COALESCE(SUM(oi.quantity), 0) FROM order_items oi
                     JOIN orders o ON o.id = oi.order_id
                     WHERE o.status = 'confirmed' AND o.created_at >= :since)    AS total_units,
                  (SELECT COALESCE(SUM(stock), 0) FROM products)                 AS stock_units,
                  (SELECT COALESCE(SUM(stock * price), 0) FROM products)         AS stock_value
                """
            ),
            {"since": since},
        )
    ).one()

    by_category = (
        await session.execute(
            text(
                _PRODUCT_SALES
                + """
                SELECT category, COUNT(*) AS products, SUM(units_sold) AS units_sold,
                       SUM(revenue) AS revenue, SUM(stock_value) AS stock_value
                FROM product_sales GROUP BY category ORDER BY revenue DESC
                """
            ),
            params,
        )
    ).mappings().all()

    top = (
        await session.execute(
            text(_PRODUCT_SALES + "SELECT * FROM product_sales "
                 "ORDER BY revenue DESC, product_id LIMIT :top_n"),
            params,
        )
    ).mappings().all()

    low = (
        await session.execute(
            text(_PRODUCT_SALES + "SELECT * FROM product_sales WHERE current_stock <= :low "
                 "ORDER BY current_stock, units_sold DESC LIMIT :top_n"),
            params,
        )
    ).mappings().all()

    total_orders = int(totals.total_orders)
    total_revenue = Decimal(totals.total_revenue)
    return SalesReport(
        generated_at=datetime.now(UTC),
        total_orders=total_orders,
        total_units_sold=int(totals.total_units),
        total_revenue=total_revenue,
        average_order_value=(
            (total_revenue / total_orders).quantize(Decimal("0.01")) if total_orders else Decimal("0")
        ),
        total_stock_units=int(totals.stock_units),
        total_stock_value=Decimal(totals.stock_value),
        by_category=[CategorySummary(**r) for r in by_category],
        top_products=[ProductSalesRow(**r) for r in top],
        low_stock=[ProductSalesRow(**r) for r in low],
    )
