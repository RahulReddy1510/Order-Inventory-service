import asyncio, aiohttp, uuid, json, random
async def main():
    async with aiohttp.ClientSession() as s:
        base='http://127.0.0.1:8000'
        async with s.post(base+'/products',json={'sku':'MIXED-'+uuid.uuid4().hex[:8],'name':'Mixed concurrency','price':'1.00','stock':50}) as r: p=await r.json()
        gate=asyncio.Event()
        async def send(kind):
            await gate.wait()
            path='/orders' if kind=='order' else f'/products/{p["id"]}/stock'
            payload={'items':[{'product_id':p['id'],'quantity':1}]} if kind=='order' else {'delta':2}
            async with s.post(base+path,json=payload) as r: return kind,r.status,await r.json()
        kinds=['order']*50+['restock']*20
        random.Random(42).shuffle(kinds)
        tasks=[asyncio.create_task(send(k)) for k in kinds]
        gate.set(); rr=await asyncio.gather(*tasks)
        async with s.get(base+f'/products/{p["id"]}') as r: final=await r.json()
        orders=sum(k=='order' and status==201 for k,status,b in rr)
        restocks=sum(k=='restock' and status==200 for k,status,b in rr)
        print(json.dumps({'orders':orders,'restocks':restocks,'final_stock':final['stock'],'expected':50+restocks*2-orders,'pass':orders==50 and restocks==20 and final['stock']==40},indent=2))
asyncio.run(main())
