#!/usr/bin/env python3
"""
Concurrency load test for the Order & Inventory Service.

What it does
------------
1. Seeds three fresh products via the API:
     HOT   - stock 50, targeted by --orders single-item orders (default 100)
     PAIR1 / PAIR2 - stock 30 each, targeted by --pair-orders two-item orders
             whose items are sent in RANDOM order (exercises deadlock avoidance)
2. Releases, at the same instant:
     * all order requests
     * --reports slow reporting requests (each holds a DB connection
       for --report-slow-ms via pg_sleep, on top of the real aggregation)
     * a storefront "probe" that keeps calling GET /products/{id} for the
       whole run, to show reads stay fast while orders and reports are in flight
3. Verifies:
     * successful orders == min(requested, stock)       (no over-selling)
     * final stock == initial - units sold, never < 0
     * stock in Postgres matches the sum of confirmed order items fetched back
     * no unexpected status codes (only 201, 409 and retried 503)
4. Prints latency percentiles per endpoint and exits non-zero on any failure.

Usage
-----
    pip install aiohttp
    python scripts/load_test.py                      # against localhost:8000
    python scripts/load_test.py --orders 300 --reports 30 --base-url http://host:8000
"""

import argparse
import asyncio
import random
import statistics
import sys
import time
import uuid
from collections import Counter, defaultdict

import aiohttp

latencies: dict[str, list[float]] = defaultdict(list)
statuses: dict[str, Counter] = defaultdict(Counter)


async def timed(session: aiohttp.ClientSession, label: str, method: str, url: str, **kw):
    t0 = time.perf_counter()
    async with session.request(method, url, **kw) as resp:
        body = await resp.json(content_type=None)
        status = resp.status
    latencies[label].append((time.perf_counter() - t0) * 1000)
    statuses[label][status] += 1
    return status, body


async def create_product(session, base, name, stock, price="10.00"):
    status, body = await timed(
        session, "seed", "POST", f"{base}/products",
        json={
            "sku": f"LT-{name}-{uuid.uuid4().hex[:8]}",
            "name": f"Load test {name}",
            "description": "Product created by load_test.py",
            "category": "loadtest",
            "price": price,
            "stock": stock,
        },
    )
    assert status == 201, (status, body)
    return body["id"]


async def place_order(session, base, items, start: asyncio.Event, max_retries=5):
    """Returns (status, body). 503 (lock / pool timeout) is retried with backoff."""
    await start.wait()
    for attempt in range(max_retries + 1):
        status, body = await timed(session, "POST /orders", "POST", f"{base}/orders",
                                   json={"items": items})
        if status != 503 or attempt == max_retries:
            return status, body
        await asyncio.sleep(0.05 * 2 ** attempt + random.random() * 0.05)
    return status, body


async def run_report(session, base, slow_ms, start: asyncio.Event):
    await start.wait()
    return await timed(session, "GET /reports/sales", "GET",
                       f"{base}/reports/sales?simulate_slow_ms={slow_ms}")


async def storefront_probe(session, base, product_id, start, stop: asyncio.Event):
    await start.wait()
    while not stop.is_set():
        await timed(session, "GET /products/{id} (probe)", "GET", f"{base}/products/{product_id}")
        await asyncio.sleep(0.02)


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    k = max(0, min(len(values) - 1, round(p / 100 * (len(values) - 1))))
    return values[k]


async def main(args) -> int:
    base = args.base_url.rstrip("/")
    connector = aiohttp.TCPConnector(limit=0)  # no client-side cap: real concurrency
    timeout = aiohttp.ClientTimeout(total=120)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # ---------- 1. seed ----------
        hot_stock, pair_stock = args.hot_stock, args.pair_stock
        hot = await create_product(session, base, "HOT", hot_stock, price="19.99")
        p1 = await create_product(session, base, "PAIR1", pair_stock, price="5.00")
        p2 = await create_product(session, base, "PAIR2", pair_stock, price="7.50")
        print(f"Seeded products: HOT={hot} (stock {hot_stock}), "
              f"PAIR1={p1}, PAIR2={p2} (stock {pair_stock} each)")

        # ---------- 2. fire everything at once ----------
        start, stop = asyncio.Event(), asyncio.Event()
        order_tasks = [
            asyncio.create_task(place_order(session, base, [{"product_id": hot, "quantity": 1}], start))
            for _ in range(args.orders)
        ]
        pair_tasks = []
        for _ in range(args.pair_orders):
            items = [{"product_id": p1, "quantity": 1}, {"product_id": p2, "quantity": 1}]
            random.shuffle(items)  # opposite lock order requests -> deadlock bait
            pair_tasks.append(asyncio.create_task(place_order(session, base, items, start)))
        report_tasks = [
            asyncio.create_task(run_report(session, base, args.report_slow_ms, start))
            for _ in range(args.reports)
        ]
        probe = asyncio.create_task(storefront_probe(session, base, hot, start, stop))

        print(f"Firing {args.orders} single-item orders + {args.pair_orders} two-item orders "
              f"+ {args.reports} reports (each >= {args.report_slow_ms} ms) concurrently...")
        t0 = time.perf_counter()
        start.set()
        order_results = await asyncio.gather(*order_tasks)
        orders_done = time.perf_counter() - t0
        pair_results = await asyncio.gather(*pair_tasks)
        report_results = await asyncio.gather(*report_tasks)
        stop.set()
        await probe
        wall = time.perf_counter() - t0

        # ---------- 3. verify ----------
        failures: list[str] = []

        def check(cond: bool, msg: str):
            print(("  PASS  " if cond else "  FAIL  ") + msg)
            if not cond:
                failures.append(msg)

        async def stock_of(pid):
            _, body = await timed(session, "verify", "GET", f"{base}/products/{pid}")
            return body["stock"]

        async def units_in_orders(results, pid):
            ids = [b["id"] for s, b in results if s == 201]
            fetched = await asyncio.gather(
                *(timed(session, "verify", "GET", f"{base}/orders/{i}") for i in ids)
            )
            return sum(it["quantity"] for _, o in fetched for it in o["items"]
                       if it["product_id"] == pid)

        hot_ok = sum(1 for s, _ in order_results if s == 201)
        hot_409 = sum(1 for s, _ in order_results if s == 409)
        pair_ok = sum(1 for s, _ in pair_results if s == 201)
        reports_ok = sum(1 for s, _ in report_results if s == 200)
        unexpected = [s for s, _ in order_results + pair_results if s not in (201, 409)]

        hot_final, p1_final, p2_final = await asyncio.gather(stock_of(hot), stock_of(p1), stock_of(p2))
        hot_units = await units_in_orders(order_results, hot)
        p1_units = await units_in_orders(pair_results, p1)
        p2_units = await units_in_orders(pair_results, p2)

        print("\nResults")
        print(f"  Single-item orders: {hot_ok} created, {hot_409} rejected (409 out of stock), "
              f"{len(unexpected)} other")
        print(f"  Two-item orders:    {pair_ok} created")
        print(f"  Reports:            {reports_ok}/{args.reports} succeeded")
        print(f"  Final stock:        HOT={hot_final}, PAIR1={p1_final}, PAIR2={p2_final}")
        print(f"  Orders finished in {orders_done:.2f}s; whole run {wall:.2f}s\n")

        print("Checks")
        check(hot_ok == min(args.orders, hot_stock),
              f"HOT: exactly {min(args.orders, hot_stock)} orders succeeded (got {hot_ok})")
        check(hot_final == hot_stock - hot_ok, f"HOT: final stock {hot_final} == {hot_stock} - {hot_ok}")
        check(hot_final >= 0, "HOT: stock never negative")
        check(hot_units == hot_ok, f"HOT: units in confirmed orders ({hot_units}) == successful orders")
        check(pair_ok == min(args.pair_orders, pair_stock),
              f"PAIR: exactly {min(args.pair_orders, pair_stock)} two-item orders succeeded (got {pair_ok})")
        check(p1_final == p2_final == pair_stock - pair_ok,
              f"PAIR: both products at {pair_stock - pair_ok} (got {p1_final}, {p2_final})")
        check(p1_units == p2_units == pair_ok, "PAIR: order items match stock movement")
        check(not unexpected, f"No unexpected order status codes {Counter(unexpected) or ''}")
        check(reports_ok == args.reports, "All reports completed while orders were running")
        probe_p95 = pct(latencies["GET /products/{id} (probe)"], 95)
        check(probe_p95 < args.max_probe_p95_ms,
              f"Storefront reads stayed responsive (p95 {probe_p95:.0f} ms < {args.max_probe_p95_ms} ms)")

        print("\nLatency (ms)")
        print(f"  {'endpoint':32} {'n':>5} {'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}")
        for label in ("POST /orders", "GET /reports/sales", "GET /products/{id} (probe)"):
            v = latencies[label]
            print(f"  {label:32} {len(v):>5} {statistics.median(v):>8.1f} {pct(v, 95):>8.1f} "
                  f"{pct(v, 99):>8.1f} {max(v):>8.1f}")
        print("\nStatus codes")
        for label in ("POST /orders", "GET /reports/sales", "GET /products/{id} (probe)"):
            print(f"  {label:32} {dict(statuses[label])}")

        print("\n" + ("ALL CHECKS PASSED" if not failures else f"{len(failures)} CHECK(S) FAILED"))
        return 0 if not failures else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--orders", type=int, default=100, help="single-item orders on HOT")
    ap.add_argument("--hot-stock", type=int, default=50)
    ap.add_argument("--pair-orders", type=int, default=40, help="two-item orders on PAIR1+PAIR2")
    ap.add_argument("--pair-stock", type=int, default=30)
    ap.add_argument("--reports", type=int, default=20)
    ap.add_argument("--report-slow-ms", type=int, default=1000)
    ap.add_argument("--max-probe-p95-ms", type=float, default=500)
    sys.exit(asyncio.run(main(ap.parse_args())))
