# Interview task checklist against the uploaded project

Reviewed: 10 October 2026.

UPDATE: Remaining runtime checks have now been executed. See [latest test results](review-evidence/TEST_RESULTS.md): 6 unit tests passed, 25/26 functional checks passed, supplied load test passed, synchronized warehouse/order test passed, slow-report overlap and Redis recovery passed. Explicit-null product PATCH returned 500 and remains unresolved. The original item-by-item review below records the earlier baseline; its runtime-pending labels are superseded by the latest results.
Source: C:/Users/Rahul Reddy/Downloads/Technical_Interview_Task.pdf.
Project: zip-preview/order-inventory-service, extracted from the uploaded ZIP and running at http://localhost:8001/docs. This review does not use the older sibling project.

Legend: VERIFIED = observed in local execution; PRESENT = implementation/documentation inspected, but not independently exercised for this requirement; PENDING = evidence or action still needed. A checked box means the artifact or implementation exists, not that all possible behavior is proven.

## Functional requirements (PDF sections 2-4)

- [x] VERIFIED: Working FastAPI service. API, PostgreSQL and Redis started locally; health reported all OK.
- [x] VERIFIED: Create products with relevant fields. POST /products created PROVE-1 with name, description, category, price 999.00 and stock 10; returned 201 and ID 2.
- [x] PRESENT: Update product metadata. PATCH /products/{product_id} calls the update service; not manually tested yet. Evidence: app/api/products.py, app/services/products.py.
- [x] PRESENT: Warehouse stock updates. POST /products/{product_id}/stock uses an atomic SQL adjustment and refuses negative stock. Not manually exercised. Evidence: app/services/products.py.
- [x] VERIFIED: Storefront product retrieval. GET /products/2 returned the created laptop and its current stock. Paginated list route is also present.
- [x] VERIFIED/PRESENT: Search by name and description. Redis indexes both fields; searching laptop and mech key worked. Description-only matching was inspected in code but not separately tested. Evidence: app/services/search.py.
- [x] VERIFIED: Place a single-product order and reduce stock. Three laptops produced confirmed order ID 2 for 2997.00; stock changed from 10 to 7.
- [x] PRESENT: Place multi-product orders atomically. Service checks all shortages before decrementing inside one transaction. Multi-product runtime test remains outstanding. Evidence: app/services/orders.py.
- [x] PRESENT: Handle simultaneous orders for the same product safely. SELECT FOR UPDATE, ascending product-ID lock order and nonnegative database constraint are implemented. Concurrent correctness has not been independently demonstrated in this session.
- [x] VERIFIED: Retrieve order details. GET /orders/2 returned quantity 3 and unit price 999.00.
- [x] VERIFIED: Reporting endpoint aggregates products and orders. GET /reports/sales returned correct laptop revenue 2997.00 and stock value 6993.00. Overall revenue 3176.80 includes the earlier keyboard test.
- [x] PRESENT: Reporting is heavier than ordinary reads. Multiple aggregate queries, joins and CTEs are implemented; simulate_slow_ms holds a reporting connection using PostgreSQL pg_sleep. Slow-report behavior was not exercised here.
- [x] VERIFIED: Sequential over-selling guard. Requesting 8 laptops with 7 remaining returned 409. Stock remained 7 and order count/revenue did not increase. This is not a substitute for concurrent testing.

## Concurrency and connection requirements (PDF sections 3, 4.1, 5.1-5.2)

- [x] PRESENT: Async endpoints and asynchronous I/O. API routes use async def; SQLAlchemy/asyncpg and redis.asyncio calls are awaited. No obvious synchronous network/database calls were found in the reviewed request paths.
- [x] PRESENT: Shared PostgreSQL database for storefront, warehouse and reporting. Both engines target the same database URL.
- [x] PRESENT: Bounded connection strategy. Per worker: OLTP maximum 20 connections, reporting maximum 3; two workers give a 46-connection application budget. Pool waits, pre-ping, recycling and server statement timeouts are configured. Evidence: app/db/database.py, app/core/config.py.
- [x] PRESENT: Heavy reports isolated from transactional pool capacity. Separate reporting engine uses REPEATABLE READ / read-only transactions. This isolates connection capacity, but does not guarantee zero CPU or I/O contention on the shared database.
- [x] PRESENT: Consistent writes and bounded lock waits. Orders use transactions and row locks; warehouse changes use atomic UPDATE; database stock constraint adds a guard.
- [x] PRESENT: Concurrent load-test script. scripts/load_test.py launches 100 single-item orders, 40 two-item orders and 20 slow reports together, plus a storefront probe; checks final stock and confirmed order items.
- [x] PRESENT: Short note on observed load-test results. README has standard and heavier-run results. These are supplied project claims, not results reproduced during this review.
- [ ] PENDING VERIFICATION: Run the supplied load test against port 8001 and retain the output to independently verify responsiveness, stock correctness and report completion under simultaneous load.

## Search and infrastructure (PDF sections 5.3-5.4)

- [x] VERIFIED/PRESENT: Search uses an indexing service, not PostgreSQL LIKE/ILIKE. app/services/search.py issues RediSearch queries; local search returned the expected products. Large-catalog latency was not benchmarked.
- [x] PRESENT: Search synchronization strategy. Background tasks update Redis after database commits; startup/admin reindex can repair drift. README explains eventual consistency and outbox/CDC alternatives.
- [x] PRESENT: README explains the search choice and scaling rationale, including inverted indexing and offloading search traffic from PostgreSQL.
- [x] VERIFIED: Full stack can start with one command. Dockerfile and docker-compose.yml are supplied. The preview was built using an isolated compose.preview.yaml because port 8000 was already occupied: docker compose -p uploaded-inventory-preview -f compose.preview.yaml up -d --build.
- [x] VERIFIED: Swagger/OpenAPI interface is available at http://localhost:8001/docs.

## Documentation and evaluation (PDF sections 7-8, 10)

- [x] PRESENT: Setup/run instructions, dependencies, endpoint examples and load-test instructions in README.
- [x] PRESENT: Architecture overview and directory map in README.
- [x] PRESENT: Connection-handling decisions, pool budget, locking approach, timeouts and alternatives explained in README.
- [x] PRESENT: Search decisions, synchronization limitations and alternatives explained in README.
- [x] PRESENT: Assumptions documented: currency, immediate confirmation, price snapshots, order limits, dedicated stock updates and eventual search consistency.
- [x] PRESENT: More-time improvements documented: migrations, transactional outbox/CDC, idempotency, integration tests, PgBouncer, replicas and observability.
- [x] PRESENT: Separation of concerns. API routers, services, database/models, schemas and configuration/errors are separate modules. This supports the code-structure criterion; it is not an exhaustive code audit.
- [x] PRESENT: Six descriptive incremental commit entries in the ZIP's .git/logs/HEAD: scaffold; database layer; domain services; API/search; load test; README. This is local history evidence; the published GitHub history was not verified.
- [ ] PENDING/UNVERIFIED: Push to a public or shared-access GitHub repository. The ZIP's Git config has no remote, and no repository URL was provided. This does not establish that no remote repository exists elsewhere.
- [ ] PENDING/UNVERIFIED: Reply to the interviewer with the repository URL. No evidence of submission is available here.
- [ ] UNVERIFIABLE: Meet the deadline. PDF section 1 estimates up to one day of work; section 9 states a maximum of two days from receipt. Receipt time is unknown.

## Explicitly out of scope (PDF section 6)

No completion work is required for authentication/authorization, accounts, payments, a custom frontend, Kubernetes or cloud deployment. Swagger is sufficient. PgBouncer is an example/alternative, not a mandatory dependency.

## Review caveats and useful finishing checks

1. Reproduce the load test before declaring concurrent correctness and responsiveness verified. Current manual requests were sequential.
2. The load-test script labels reports as completed while orders were running, but its assertion only checks all reports succeeded. It launches work together but does not prove completion overlap. Its orders_done timestamp covers single-item tasks, not necessarily all two-item tasks. Storefront HTTP status counts are printed but not asserted. Tighten these claims/checks if using their output as evidence.
3. Redis updates are unversioned background writes. Concurrent writes can arrive out of order and leave stale search stock until a reindex. The README acknowledges eventual consistency; PostgreSQL remains authoritative for order acceptance. This is a documented trade-off, not a missing mandatory technology.
4. Manually check product PATCH, warehouse restock/write-off, description-only search and multi-product rollback. They are implemented but were not covered by our prior smoke tests.
5. README lists host database ports as 5432/6379, while the original docker-compose.yml publishes 5433/6380. Align these setup details. The preview uses internal database ports and API port 8001.
6. Six search-query unit tests are supplied; they were not run in this checklist review. Unit tests are useful but not explicitly required by the PDF.
7. The PDF evaluation table visibly overflows the right page edge. This checklist uses readable criteria together with the explicit requirements in sections 3-7, without inventing the clipped wording.

Overall: The requested implementation and documentation are present, and the core sequential flows work. Independent load-test evidence and GitHub delivery remain unconfirmed; do not label the complete assessment submitted or fully verified yet.

