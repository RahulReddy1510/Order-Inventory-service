# Order & Inventory Service

A FastAPI backend shared by three concurrent consumers — a read-heavy **storefront**, a **warehouse** app that writes stock, and a heavy **reporting** job — all on one PostgreSQL database, with near-instant product search served from **RediSearch**.

The focus is correctness under concurrency: no over-selling, no deadlocks, no connection exhaustion, and slow reports that can't starve order placement.

---

## Quick start

Requirements: Docker with Docker Compose.

```bash
docker compose up --build
```

That starts three services:

| Service    | Image                               | Port |
|------------|-------------------------------------|------|
| `api`      | built from `Dockerfile` (2 uvicorn workers) | 8000 |
| `postgres` | `postgres:16-alpine`                | 5432 |
| `redis`    | `redis/redis-stack-server` (includes RediSearch) | 6379 |

Tables are created and the search index is built automatically on startup.

- Swagger UI: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Run the load test

In a second terminal, with the stack running:

```bash
pip install -r requirements-dev.txt
python scripts/load_test.py
```

Options: `--orders`, `--hot-stock`, `--pair-orders`, `--pair-stock`, `--reports`, `--report-slow-ms`, `--base-url`. The script exits non-zero if any check fails.

### Unit tests

```bash
pytest -q
```

### Try it by hand

```bash
# create a product
curl -X POST localhost:8000/products -H 'content-type: application/json' \
  -d '{"sku":"KB-1","name":"Mechanical Keyboard","description":"RGB backlit, hot-swap switches","category":"peripherals","price":"89.90","stock":5}'

# search (served by Redis, not Postgres)
curl 'localhost:8000/products/search?q=mech key&in_stock=true'

# place an order
curl -X POST localhost:8000/orders -H 'content-type: application/json' \
  -d '{"items":[{"product_id":1,"quantity":2}]}'

# heavy report
curl localhost:8000/reports/sales
```

---

## API

| Method | Path | Consumer | Notes |
|---|---|---|---|
| `POST` | `/products` | warehouse | Create. 409 on duplicate SKU. Indexed for search after commit. |
| `GET` | `/products` | storefront | Paged list (`limit`, `offset`). |
| `GET` | `/products/{id}` | storefront | |
| `PATCH` | `/products/{id}` | warehouse | Partial update of name/description/category/price. Re-indexed. |
| `POST` | `/products/{id}/stock` | warehouse | `{"delta": n}` restock (+) or write-off (−). 409 if it would go negative. |
| `GET` | `/products/search` | storefront | `q`, `category`, `min_price`, `max_price`, `in_stock`, `limit`, `offset`. RediSearch only. |
| `POST` | `/orders` | storefront | One or more items, all-or-nothing. 201 / 404 / 409 insufficient stock / 503 retry. |
| `GET` | `/orders/{id}` | storefront | Order with line items and price snapshot. |
| `GET` | `/reports/sales` | reporting | Totals, by-category, top products, low stock. `since_days`, `top_n`, `low_stock_threshold`, `simulate_slow_ms` (testing aid). |
| `GET` | `/health` | ops | Checks Postgres and Redis. |
| `GET` | `/admin/pool-stats` | ops | Live status of both connection pools in the answering worker. |
| `POST` | `/admin/search/reindex` | ops | Rebuild the search index from Postgres. |

---

## Architecture

```
                     ┌──────────────────────── api (FastAPI, uvicorn × 2 workers) ────────────────────────┐
 storefront ───────▶ │  routers (app/api)  ──▶  services (app/services)  ──▶  OLTP pool  (≤20/worker) ─────┼──▶ PostgreSQL
 warehouse  ───────▶ │                                                    ──▶  Reporting pool (≤3/worker) ─┼──▶ (source of truth)
 reporting  ───────▶ │                                                                                     │
                     │  search service ── HSET after commit (background task) / FT.SEARCH ─────────────────┼──▶ Redis + RediSearch
                     └─────────────────────────────────────────────────────────────────────────────────────┘     (derived index)
```

```
app/
  main.py            app factory, lifespan (create tables, bootstrap index), router wiring
  core/config.py     settings from environment (pool sizes, timeouts, URLs)
  core/errors.py     domain errors + mapping of pool/lock/statement timeouts to HTTP
  db/database.py     the two async engines, session dependencies  ← connection strategy
  db/models.py       Product, Order, OrderItem
  schemas.py         Pydantic request/response models
  services/
    products.py      CRUD + atomic stock adjustment
    orders.py        race-safe order placement                    ← locking strategy
    reports.py       aggregate queries on a consistent snapshot
    search.py        RediSearch index, sync and querying          ← search strategy
  api/               thin routers: validate → call service → schedule index sync
scripts/load_test.py
tests/               unit tests for the search query builder
```

Routers stay thin; all database and Redis logic lives in services, so it can be tested or reused (e.g. from a worker) without HTTP.

Every endpoint is `async`, and every I/O call is awaited through async drivers (`asyncpg`, `redis.asyncio`). No blocking calls run on the event loop.

---

## Connection handling

**Choice:** SQLAlchemy 2.0 `AsyncEngine` on `asyncpg`, with **two separate bounded connection pools** — one for transactional traffic and one reserved for reporting. All settings are in `app/core/config.py` and overridable by environment variable.

| | OLTP pool | Reporting pool |
|---|---|---|
| Used by | product reads/writes, stock adjustments, orders | `/reports/*` only |
| `pool_size` / `max_overflow` | 10 / 10 → max **20** per worker | 3 / 0 → max **3** per worker |
| `pool_timeout` | 5 s, then **503 + Retry-After** | 10 s |
| Server `statement_timeout` | 5 s | 30 s |
| Transaction mode | READ COMMITTED (default) | **REPEATABLE READ, READ ONLY** |

### Why this prevents exhaustion

The number of Postgres connections the service can ever open is fixed and calculable:

```
workers × (OLTP max + reporting max) = 2 × (20 + 3) = 46   <   max_connections = 100
```

Leftover capacity covers `psql` sessions, migrations, and a rolling restart that briefly runs old and new processes side by side. When traffic exceeds pool capacity, requests **wait in the pool queue** (cheap, in-process) instead of opening more connections. If they wait longer than `pool_timeout`, they fail fast with 503 rather than hanging — back-pressure instead of collapse. `pool_pre_ping` and `pool_recycle` replace connections that Postgres or the network dropped.

### Why heavy queries can't block others

- **Bulkhead.** Reports get their own 3-connection pool. Twenty slow reports arriving at once can only occupy 3 connections per worker; the rest queue for *reporting* connections, while order placement and storefront reads keep using the untouched OLTP pool. The load test shows this directly: orders all finish in well under a second while reports are still queued behind each other.
- **MVCC.** Reports are plain reads, and in Postgres readers never block writers. REPEATABLE READ gives every figure in one report the same snapshot, so totals add up even while orders commit mid-report.
- **Timeouts.** `statement_timeout` is set server-side per pool, so a runaway query is killed by Postgres instead of pinning a connection forever.

### Keeping writes consistent (no over-selling)

`POST /orders` (`app/services/orders.py`):

1. `SELECT … FROM products WHERE id IN (…) ORDER BY id FOR UPDATE` locks every product in the order. A concurrent order for the same product **blocks here until the first commits, then reads the committed stock**, so check-then-decrement can't interleave.
2. Locks are taken in **ascending id order**, so two multi-product orders that share products always lock in the same order — no deadlocks. (The load test sends two-item orders with items in random order to provoke exactly this.)
3. `SET LOCAL lock_timeout = 3s`: if a row is too hot, the request fails fast with 503 instead of holding a pooled connection indefinitely.
4. All-or-nothing: any shortage → 409 with per-item details, full rollback.
5. `CHECK (stock >= 0)` on the table is a final database-level guard.

Warehouse adjustments use a single atomic statement, `UPDATE … SET stock = stock + :delta WHERE id = :id AND stock + :delta >= 0`, which needs no explicit lock because Postgres evaluates it under the row lock the UPDATE takes.

**Why pessimistic locking rather than optimistic (version column + retry)?** On a hot product, optimistic concurrency turns contention into a storm of retries. Row locks queue the contenders in the database and each transaction is a few milliseconds, which is the better trade for inventory.

### Alternatives considered

- **PgBouncer (transaction pooling)** in front of Postgres. This is the right next step when many app instances share one database: it caps server connections globally rather than per process. It isn't needed at this scale, and with asyncpg it requires disabling prepared-statement caching (`statement_cache_size=0`) — a real cost. The pools here are sized so it can be added later without code changes beyond that one setting.
- **Read replica for reporting.** The cleanest isolation in production (reports never touch the primary). Out of scope for a local stack; the separate reporting engine is already the seam where a replica URL would plug in.

---

## Product search

**Choice:** **RediSearch** (the Redis Stack search module), with Postgres remaining the source of truth.

### How it works

Each product is stored as a Redis hash `product:{id}`. One `FT.CREATE` index covers the `product:` prefix, so Redis updates the index automatically on every `HSET`:

| Field | Type | Purpose |
|---|---|---|
| `name` | TEXT, weight 5 | full-text, stemmed; name matches rank above description matches |
| `description` | TEXT | full-text |
| `sku`, `category` | TAG | exact-match filters |
| `price` | NUMERIC, sortable | range filter |
| `stock` | NUMERIC | `in_stock=true` filter |

User input is tokenised rather than passed through: every word becomes a prefix term (`"mech key"` → `mech* key*`), which gives search-as-you-type behaviour and also neutralises RediSearch query syntax, so user text can't inject operators.

`GET /products/search` never touches Postgres.

### Why it scales better than `LIKE` / `ILIKE`

`ILIKE '%term%'` can't use a B-tree index, so Postgres scans every row on every search, and that scan competes with orders for the same database. RediSearch keeps an **in-memory inverted index**: a lookup reads only the posting lists for the query terms, so its cost depends on the number of matches, not the catalogue size. It also provides stemming, relevance ranking and field weights, and it moves search traffic — the storefront's heaviest read — off the database entirely.

(Postgres `pg_trgm` GIN indexes or built-in full-text search would also avoid sequential scans and need no extra service. I picked RediSearch because it takes the read load off Postgres, which is the shared bottleneck in this scenario.)

### Keeping the index in sync

- Writes go to **Postgres first**. After the commit, the changed fields are pushed to Redis in a FastAPI background task, so the client doesn't wait on Redis. Orders and stock adjustments push only the new `stock` values.
- A Redis failure is logged and never fails the write — the index is derived data.
- On startup, the index is **rebuilt from Postgres** (one worker takes a Redis lock; the rows are streamed in batches of 500 through a pipeline). `POST /admin/search/reindex` does the same on demand, repairing any drift.
- If Redis is down at startup, the API still starts and serves products and orders; only search is unavailable until Redis returns and a reindex runs.

**Trade-off:** this is a dual write, so there's a short window where search can be stale (milliseconds normally, until the next reindex if a Redis write failed). Search results are treated as a hint: stock shown in search may lag, but **order placement always checks stock in Postgres under a lock**, so stale search data can never cause over-selling. The production fix for the dual-write gap is a transactional outbox or CDC (e.g. Debezium on the Postgres WAL) feeding the index.

---

## Load test results

`scripts/load_test.py` seeds fresh products, then releases at the same instant:

- **100** single-item orders against a product with **50** in stock
- **40** two-item orders against two products with **30** each, items in **random order** (deadlock bait)
- **20** reports, each holding its connection ≥ 1 s (`simulate_slow_ms=1000`) plus the real aggregation
- a storefront probe calling `GET /products/{id}` every 20 ms throughout

**Expected outcome:** exactly 50 single-item orders and 30 two-item orders succeed, the rest get 409, all three products end at stock 0, the units in the confirmed orders match the stock that left, there are no deadlocks or 5xx errors, every report completes, and storefront reads stay fast.

Observed run (2 uvicorn workers, Postgres 16, on a development machine):

```
Results
  Single-item orders: 50 created, 50 rejected (409 out of stock), 0 other
  Two-item orders:    30 created
  Reports:            20/20 succeeded
  Final stock:        HOT=0, PAIR1=0, PAIR2=0
  Orders finished in 0.66s; whole run 5.33s

Checks
  PASS  HOT: exactly 50 orders succeeded (got 50)
  PASS  HOT: final stock 0 == 50 - 50
  PASS  HOT: stock never negative
  PASS  HOT: units in confirmed orders (50) == successful orders
  PASS  PAIR: exactly 30 two-item orders succeeded (got 30)
  PASS  PAIR: both products at 0 (got 0, 0)
  PASS  PAIR: order items match stock movement
  PASS  No unexpected order status codes
  PASS  All reports completed while orders were running
  PASS  Storefront reads stayed responsive (p95 8 ms < 500 ms)

Latency (ms)
  endpoint                             n      p50      p95      p99      max
  POST /orders                       140    544.6    732.3    746.5    748.7
  GET /reports/sales                  20   2225.2   5175.5   5262.4   5262.4
  GET /products/{id} (probe)         188      4.1      7.6     33.0    518.4
```

**What the numbers show**

- **No over-selling and no deadlocks**, including the random-order two-item orders.
- **The bulkhead works.** All 140 orders finished in 0.66 s, while the 20 slow reports took up to ~5 s — they were queuing for the 6 reporting connections (2 workers × 3), not for the order connections. Without the separate pool, those reports would have held most of the shared pool for seconds at a time and orders would have queued behind them.
- **Storefront reads stayed fast** (p95 ≈ 8 ms) throughout.
- **Order latency is dominated by serialisation on the hot row.** 100 requests for the same product must take its lock one at a time; that's the deliberate price of correctness.

A heavier run (`--orders 500 --hot-stock 200 --pair-orders 200 --pair-stock 120 --reports 40`) also passed every check: 200 / 120 orders succeeded, all stock ended at 0, no 503s, probe p95 6 ms. With 700 orders in flight the OLTP pools were briefly saturated, and one probe request waited ~2.6 s for a connection; that is the pool queue doing its job (no connection storm, no errors), and it is the point at which you would add API instances behind PgBouncer.

---

## Assumptions

- One currency; prices are `NUMERIC(12,2)`. Order lines snapshot the unit price, so later price changes don't rewrite history.
- Orders are confirmed immediately (no payment step — out of scope). The `status` column exists so cancellations or pending states can be added.
- A product appears at most once per order; quantity per line is 1–1000; up to 50 lines per order.
- Stock is changed only by orders and the dedicated stock endpoint, not by `PATCH /products/{id}`, so every stock movement goes through a concurrency-safe path.
- Search results may lag Postgres by milliseconds; this is acceptable for browsing because orders re-check stock.
- `simulate_slow_ms` on the report endpoint is a testing aid; in production I'd remove it or hide it behind a flag.

## What I'd do with more time

- **Alembic migrations** instead of `create_all` at startup.
- **Transactional outbox** (or CDC) for search sync, to close the dual-write gap.
- **Idempotency keys** on `POST /orders`, so a client retrying after a timeout can't double-order.
- **Integration tests** (pytest + testcontainers) for the order race and the search sync, running in CI.
- **PgBouncer** and multiple API replicas, with pool sizes recomputed against the global budget.
- **Reporting on a read replica**, or pre-aggregated materialised views refreshed periodically for very large order tables.
- **Hot-SKU throughput:** for flash-sale products, reserve stock in Redis atomically (Lua `DECRBY` guard) as a fast pre-check before the Postgres transaction, to shed doomed requests early.
- **Observability:** Prometheus metrics for pool checkouts/waits, lock waits, and endpoint latency; `application_name` is already set per pool so the pools are distinguishable in `pg_stat_activity`.
