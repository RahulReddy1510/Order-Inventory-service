# Manual local test results

Tested 10 October 2026 through the Swagger browser interface at http://localhost:8001/docs.
Source: uploaded order-inventory-service.zip, extracted separately from the existing project.
Application source was not changed. compose.preview.yaml isolates the preview and binds port 8001 to localhost.

| Check | Observed result | Outcome |
|---|---|---|
| Create MANUAL-KB-01, price 89.90, stock 5 | HTTP 201, product ID 1 | Pass |
| Order 2 units of product 1 | HTTP 201, confirmed order ID 1, total 179.80 | Pass |
| Order another 4 units | HTTP 409, requested 4 / available 3 | Pass |
| Prefix search: mech key | HTTP 200, one matching product, stock 3 | Pass |
| Sales report | HTTP 200, 1 order, 2 sold, revenue 179.80, remaining stock value 269.70 | Pass |
| Health (HTTP request outside browser) | Postgres ok, Redis ok, status ok | Pass |

API logs confirmed these responses and showed no errors during the tested flows.
This was a manual smoke test, not exhaustive testing or a concurrency/load test.
The sample product and order remain available in the isolated preview database.

## Run again

From this directory:

```powershell
docker compose -p uploaded-inventory-preview -f compose.preview.yaml up -d --build
```

Stop without removing containers or test data:

```powershell
docker compose -p uploaded-inventory-preview -f compose.preview.yaml stop
```
