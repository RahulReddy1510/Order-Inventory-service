import asyncio, aiohttp, json, time, uuid
async def main():
    async with aiohttp.ClientSession() as s:
        base='http://127.0.0.1:8000'
        async with s.post(base+'/products',json={'sku':'OVERLAP-'+uuid.uuid4().hex[:8],'name':'Overlap check','price':'1.00','stock':10}) as r: p=await r.json()
        async def report():
            async with s.get(base+'/reports/sales?simulate_slow_ms=2000') as r: return r.status
        tasks=[asyncio.create_task(report()) for _ in range(12)]
        await asyncio.sleep(.2)
        t=time.perf_counter()
        async with s.post(base+'/orders',json={'items':[{'product_id':p['id'],'quantity':1}]}) as r: status=r.status; body=await r.json()
        elapsed=time.perf_counter()-t
        pending=sum(not t.done() for t in tasks)
        async with s.get(base+'/admin/pool-stats') as r: pools=await r.json()
        reports=await asyncio.gather(*tasks)
        print(json.dumps({'order_status':status,'order_seconds':elapsed,'reports_pending_at_order_completion':pending,'report_statuses':reports,'pool_snapshot':pools,'pass':status==201 and pending>0 and all(x==200 for x in reports)},indent=2))
asyncio.run(main())
