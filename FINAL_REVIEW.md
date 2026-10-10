# Final interview-task review

Verified 10 October 2026 against Technical_Interview_Task.pdf. This file supersedes earlier pending-runtime labels in INTERVIEW_TASK_CHECKLIST.md and the null-update failure in the historical test report.

## Requirement checklist

| Requirement | Final assessment and evidence |
|---|---|
| FastAPI with async endpoints | Implemented; local API and database health verified |
| Create and update products | Passed HTTP checks; explicit null updates now return 422 |
| Warehouse stock changes | Restock, write-off and negative-stock rejection passed |
| Fast name/description search | Redis search, description-only matching, filters and pagination passed; no SQL LIKE search path |
| Single- and multi-product orders | Creation, retrieval, stock deductions, rollback and price snapshots passed |
| Concurrent stock correctness | Supplied load test passed; synchronized 50 orders plus 20 restocks produced expected stock 40 |
| Heavier reporting endpoint | Aggregate totals passed; slow reports ran alongside orders |
| Safe connection handling | Separate bounded pools, transaction locking and timeouts implemented and explained; an order finished in 16.85 ms while 12 slow reports remained pending |
| Concurrent load test and results | Script included; 140 order attempts plus 20 reports passed; detailed observed results saved in review-evidence/TEST_RESULTS.md |
| Single-command local startup | Compose and Dockerfile included; preview build/start verified; default Compose uses host API port 8000, isolated preview uses 8001 |
| README setup and architecture | Present; host database ports corrected to PostgreSQL 5433 and Redis 6380 |
| README rationale and trade-offs | Connection pools, locking, search synchronization, alternatives, assumptions and future improvements documented |
| Readable separation of concerns | Routes, services, models, schemas and configuration separated |
| Incremental Git history | Six original descriptive commit entries retained in packaged Git metadata; final edits are saved working-tree changes, not new commits |
| GitHub publication and URL reply | Not performed, per user instruction; remains a submission step |
| Deadline | Cannot verify without receipt time; brief specifies maximum two days in its timeline section |
| Out-of-scope work | Authentication, payments, custom frontend and cloud deployment are not required |

## Final fix verification

- 12 tests passed in 0.59 seconds: six original search tests plus six product-update regression cases.
- Live PATCH checks for null name, description, category and price each returned 422.
- Empty PATCH returned 200; omitted fields and rejected updates left the product unchanged.
- PostgreSQL and Redis health remain OK.
- Source fix is app/schemas.py; regression tests are tests/test_product_update.py.

## Limits and delivery state

The load run's storefront p95 was 9.6 ms, but its maximum read latency was 3056.7 ms. It proves the recorded workload passed, not that every request is fast. Search remains eventually consistent; failed Redis indexing was successfully repaired by reindexing. Large-catalog search latency and arbitrary-load behavior were not proven.

Application implementation requirements are covered by the inspected code and recorded tests. GitHub publication, submission URL and deadline confirmation remain outstanding. No push or new commit was made. Earlier reports remain as historical evidence rather than being rewritten to hide the original failure.

Both cleaned archives are refreshed from the final project files while retaining original Git metadata. Virtual environments, Python caches, compiled bytecode and Mac metadata are excluded. The older sibling project is not the reviewed source; use this folder or the refreshed ZIP.
