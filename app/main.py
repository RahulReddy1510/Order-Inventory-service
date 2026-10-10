import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import admin, orders, products, reports
from app.core.errors import register_error_handlers
from app.db.database import dispose_engines, init_models
from app.services import search

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_models()
    try:
        await search.reindex_on_startup()
    except Exception:  # noqa: BLE001 - app still serves orders if Redis is down
        log.exception("Search index bootstrap failed; search unavailable until reindex")
    yield
    await search.close_client()
    await dispose_engines()


app = FastAPI(
    title="Order & Inventory Service",
    version="1.0.0",
    description="Concurrent-safe orders and inventory with RediSearch-backed product search.",
    lifespan=lifespan,
)
register_error_handlers(app)

app.include_router(products.router)
app.include_router(orders.router)
app.include_router(reports.router)
app.include_router(admin.router)
