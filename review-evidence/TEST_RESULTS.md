# Remaining test results - 10 October 2026

Target: uploaded project, Docker preview at http://localhost:8001.
Application source unchanged. No GitHub push, commit, or submission performed.
Functional requests were executed directly over HTTP, like the earlier command-based checks; this round was not a browser-click walkthrough.

## Unit tests

Command inside the API container: python -m pytest -q tests
Result: 6 passed in 1.43 seconds.
Installed the declared pytest 8.3.4 and aiohttp 3.11.11 test dependencies in the running preview container. They are temporary container changes, not application dependency edits.

## Functional API checks

25 of 26 checks passed. Full request-result evidence: remaining-checks.json. Reproducible checks: remaining_checks.py.

Passed:
- Product name/description/price update and retrieval.
- Redis synchronization after metadata update.
- Description-only search.
- Warehouse restock (+5) and write-off (-3).
- Warehouse adjustment below zero rejected with 409; stock unchanged.
- Multi-product order: two units at 12.50 plus one at 20.00 = 45.00; both stocks correct.
- Multi-product shortage: entire order rolled back; both stocks unchanged.
- Missing product in a multi-product order: 404 with no stock deducted.
- Order price snapshot: changing current price to 15.00 preserved historical unit price 12.50 and order total 45.00.
- Category, price range, in-stock and pagination search filters; no-match search.
- Missing product/order IDs: 404.
- Duplicate SKU: 409.
- Zero/negative order quantity, negative price, duplicate product lines, empty order and zero stock delta: 422.
- Concurrent order/warehouse requests: final stock matched stock movements.

Failed:
- PATCH /products/3 with {"name":null} returned HTTP 500 {"detail":"Database error"}; expected a client validation rejection such as 422.
- Root cause visible in app/schemas.py and app/services/products.py: ProductUpdate allows explicit None; update_product writes it to a nonnullable database field.
- This is an unresolved application defect. No fix was applied as part of this testing request.

## Supplied concurrent load test

Command inside the preview API container:
python scripts/load_test.py --base-url http://127.0.0.1:8000

The internal port 8000 is the same uploaded API exposed on host port 8001.

Results:
- 100 single-item order attempts: 50 created, 50 rejected for insufficient stock.
- 40 two-item order attempts: 30 created, 10 rejected.
- All three load-test products ended at stock 0; fetched order line quantities matched stock deductions.
- 20/20 slow reports returned 200.
- All 195 storefront probes returned 200.
- No unexpected order responses: 80 HTTP 201 and 60 HTTP 409.
- Script exited successfully with ALL CHECKS PASSED.

| Endpoint | p50 ms | p95 ms | p99 ms | Maximum ms |
|---|---:|---:|---:|---:|
| Orders | 3016.2 | 3357.1 | 3369.4 | 3372.0 |
| Reports | 6138.9 | 8165.3 | 8166.8 | 8166.8 |
| Storefront reads | 5.0 | 9.6 | 13.5 | 3056.7 |

The p95 responsiveness criterion passed, but the slowest read took about 3.06 seconds. This does not establish that every request remains fast. Runtime was 8.24 seconds; the script's 3.15-second orders_done metric covers its single-item tasks. Functional checks were also active on separate products during this run.

## Additional concurrency verification

To address limitations in the supplied load-test assertions, overlap_check.py directly measured order completion with reports outstanding:
- An order returned 201 in 16.85 ms while all 12 slow reports were pending.
- All 12 reports subsequently returned 200.
- Pool snapshot from one worker showed 3 reporting connections checked out, no reporting overflow, and 0 OLTP connections checked out at snapshot time.

mixed_concurrency.py released a shuffled set of 50 orders and 20 warehouse restocks from the same start gate:
- Initial stock 50; each restock added 2.
- All 50 orders and all 20 restocks succeeded.
- Final stock 40 = 50 + (20 * 2) - 50.

These checks support correctness for the tested workloads, not an unlimited-load guarantee.

## Redis recovery

Briefly stopped only uploaded-inventory-preview-redis-1 and restarted it in a finally block.
- Health correctly reported PostgreSQL OK / Redis connection error / degraded.
- Creating a new recovery product while Redis was down still returned 201 (product ID 12).
- After restart, that product was absent from search, demonstrating the failed index write.
- POST /admin/search/reindex indexed 11 products at that moment.
- The product then appeared in search with correct stock 4 and price 7.
- Final health reported PostgreSQL OK / Redis OK / status OK.

This tested failed-write recovery without deleting Redis data or existing user products.

## Final state

Preview remains running, and test products/orders remain in its isolated database. Sales-report totals now include these tests.
Remaining issues: explicit-null PATCH validation defect; observed worst-case read latency; previously noted README host-port mismatch. GitHub delivery and deadline compliance are not runtime tests and remain outside this execution.
