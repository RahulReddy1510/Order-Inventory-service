"""
Product search backed by RediSearch.

Index layout
------------
Each product is a Redis hash at key ``product:{id}``. A RediSearch index over
the ``product:`` prefix is maintained automatically by Redis whenever a hash
is written, so "indexing" a product is a single HSET.

    name         TEXT (weight 5)   - full-text, stemmed, prefix-searchable
    description  TEXT (weight 1)
    sku          TAG               - exact match
    category     TAG               - exact-match filter
    price        NUMERIC SORTABLE  - range filter
    stock        NUMERIC           - "in stock only" filter

Sync with Postgres (Postgres is the source of truth)
----------------------------------------------------
* Product create/update, stock adjustments and orders write to Postgres first,
  commit, then push the changed fields to Redis in a background task (after
  the HTTP response has been sent).
* If a Redis write fails we log it and carry on; the index is a derived cache.
* On startup (and via POST /admin/search/reindex) the whole index is rebuilt
  from Postgres, which repairs any drift.
"""

import logging
import re
from decimal import Decimal

import redis.asyncio as redis
from redis.commands.search.field import NumericField, TagField, TextField
from redis.commands.search.indexDefinition import IndexDefinition, IndexType
from redis.commands.search.query import Query
from redis.exceptions import ResponseError
from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import OltpSession
from app.db.models import Product

log = logging.getLogger(__name__)
settings = get_settings()

KEY_PREFIX = "product:"
INDEX = settings.search_index_name

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        # Bounded pool here too: a burst of searches can't open unlimited sockets.
        _client = redis.from_url(settings.redis_url, decode_responses=True, max_connections=50)
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ensure_index() -> None:
    client = get_client()
    try:
        await client.ft(INDEX).info()
        return
    except ResponseError:
        pass  # index does not exist yet
    schema = (
        TextField("name", weight=5.0),
        TextField("description"),
        TagField("sku"),
        TagField("category"),
        NumericField("price", sortable=True),
        NumericField("stock"),
    )
    try:
        await client.ft(INDEX).create_index(
            schema,
            definition=IndexDefinition(prefix=[KEY_PREFIX], index_type=IndexType.HASH),
        )
        log.info("Created search index %s", INDEX)
    except ResponseError as exc:
        # Another worker created it between our info() and create_index().
        if "already exists" not in str(exc).lower():
            raise


def _doc(p: Product) -> dict[str, str | int | float]:
    return {
        "id": p.id,
        "sku": p.sku,
        "name": p.name,
        "description": p.description or "",
        "category": p.category,
        "price": float(p.price),
        "stock": p.stock,
    }


async def index_product(product: Product) -> None:
    try:
        await get_client().hset(f"{KEY_PREFIX}{product.id}", mapping=_doc(product))
    except Exception:  # noqa: BLE001 - search must never break writes
        log.exception("Failed to index product %s (fix with /admin/search/reindex)", product.id)


async def update_stock(stock_by_product: dict[int, int]) -> None:
    """Push new stock levels after an order or stock adjustment."""
    try:
        pipe = get_client().pipeline(transaction=False)
        for pid, stock in stock_by_product.items():
            pipe.hset(f"{KEY_PREFIX}{pid}", "stock", stock)
        await pipe.execute()
    except Exception:  # noqa: BLE001
        log.exception("Failed to update stock in search index for %s", list(stock_by_product))


async def reindex_all(batch_size: int = 500) -> int:
    """Rebuild the index from Postgres. Streams rows so memory stays flat."""
    await ensure_index()
    client = get_client()
    count = 0
    async with OltpSession() as session:
        result = await session.stream_scalars(
            select(Product).order_by(Product.id).execution_options(yield_per=batch_size)
        )
        pipe = client.pipeline(transaction=False)
        async for product in result:
            pipe.hset(f"{KEY_PREFIX}{product.id}", mapping=_doc(product))
            count += 1
            if count % batch_size == 0:
                await pipe.execute()
                pipe = client.pipeline(transaction=False)
        await pipe.execute()
    log.info("Reindexed %d products", count)
    return count


async def reindex_on_startup() -> None:
    """Only one worker process rebuilds the index; others skip."""
    client = get_client()
    if await client.set("lock:reindex", "1", nx=True, ex=60):
        try:
            await reindex_all()
        finally:
            await client.delete("lock:reindex")
    else:
        await ensure_index()


# ---------- Querying ----------

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
_TAG_ESCAPE_RE = re.compile(r"([^\w])")


def _escape_tag(value: str) -> str:
    return _TAG_ESCAPE_RE.sub(r"\\\1", value)


def build_query(
    text: str,
    *,
    category: str | None,
    min_price: float | None,
    max_price: float | None,
    in_stock: bool,
) -> str:
    parts: list[str] = []
    # Tokenising the user input (instead of passing it through) both escapes
    # RediSearch syntax and lets us add prefix matching: "wire head" ->
    # "wire* head*" matches "Wireless Headphones" as the user types.
    tokens = _TOKEN_RE.findall(text.lower())
    if tokens:
        parts.append(" ".join(f"{t}*" if len(t) >= 2 else t for t in tokens))
    if category:
        parts.append(f"@category:{{{_escape_tag(category)}}}")
    if min_price is not None or max_price is not None:
        lo = "-inf" if min_price is None else min_price
        hi = "+inf" if max_price is None else max_price
        parts.append(f"@price:[{lo} {hi}]")
    if in_stock:
        parts.append("@stock:[1 +inf]")
    return " ".join(parts) or "*"


async def search_products(
    text: str,
    *,
    category: str | None = None,
    min_price: float | None = None,
    max_price: float | None = None,
    in_stock: bool = False,
    limit: int = 20,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    q = (
        Query(build_query(text, category=category, min_price=min_price,
                          max_price=max_price, in_stock=in_stock))
        .paging(offset, limit)
        .return_fields("id", "sku", "name", "description", "category", "price", "stock")
    )
    res = await get_client().ft(INDEX).search(q)
    hits = [
        {
            "id": int(d.id.removeprefix(KEY_PREFIX)),
            "sku": d.sku,
            "name": d.name,
            "description": d.description,
            "category": d.category,
            "price": Decimal(str(d.price)),
            "stock": int(d.stock),
        }
        for d in res.docs
    ]
    return res.total, hits
