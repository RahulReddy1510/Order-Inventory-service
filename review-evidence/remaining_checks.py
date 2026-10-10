import json, time, uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = 'http://localhost:8001'
RUN = uuid.uuid4().hex[:8]
results = []
def call(method, path, data=None):
    req = Request(BASE+path, data=json.dumps(data).encode() if data is not None else None,
                  headers={'Content-Type':'application/json'}, method=method)
    try:
        with urlopen(req, timeout=45) as r: return r.status, json.load(r)
    except HTTPError as e:
        raw=e.read().decode()
        try: body=json.loads(raw)
        except ValueError: body=raw
        return e.code, body
def check(name, condition, evidence):
    results.append({'check':name,'pass':bool(condition),'evidence':evidence})
    print(('PASS ' if condition else 'FAIL ')+name+' '+json.dumps(evidence), flush=True)
def create(label, stock=10, price='10.00'):
    data={'sku':f'QA-{RUN}-{label}','name':f'Review {label} {RUN}',
          'description':f'uniquedescription{RUN}', 'category':f'qa{RUN}', 'price':price,'stock':stock}
    s,p=call('POST','/products',data)
    assert s==201,(s,p)
    return p
def search(**kw): return call('GET','/products/search?'+urlencode(kw))
def get(p): return call('GET',f'/products/{p["id"]}')[1]
def order(items): return call('POST','/orders',{'items':items})
def line(p,n): return {'product_id':p['id'],'quantity':n}
def eventually(predicate):
    for _ in range(30):
        value=predicate()
        if value: return value
        time.sleep(.1)
    return False

a=create('A'); b=create('B',stock=2,price='20.00')
s,p=call('PATCH',f'/products/{a["id"]}',{'name':f'Updated laptop {RUN}','description':f'uniquedescription{RUN}','price':'12.50'})
check('Product PATCH and retrieval',s==200 and get(a)['price']=='12.50' and get(a)['name']==f'Updated laptop {RUN}',p)
found=eventually(lambda: any(x['id']==a['id'] and x['price'] in ('12.5','12.50') for x in search(q='Updated laptop',category=f'qa{RUN}')[1].get('items',[])))
check('Updated metadata synchronized to Redis',found,{'product_id':a['id']})
s,h=search(q=f'uniquedescription{RUN}')
check('Description-only search',s==200 and {a['id'],b['id']} <= {x['id'] for x in h.get('items',[])},h)
for delta,expected in [(5,15),(-3,12)]:
    s,p=call('POST',f'/products/{a["id"]}/stock',{'delta':delta})
    check(f'Warehouse adjustment {delta}',s==200 and p.get('stock')==expected,p)
s,p=call('POST',f'/products/{a["id"]}/stock',{'delta':-13})
check('Reject negative warehouse stock',s==409 and get(a)['stock']==12,p)
s,o=order([line(a,2),line(b,1)])
check('Multi-product order',s==201 and o.get('total_amount')=='45.00' and get(a)['stock']==10 and get(b)['stock']==1,o)
oid=o['id']
s,o=order([line(a,1),line(b,2)])
check('Multi-product shortage rolls back all stock',s==409 and get(a)['stock']==10 and get(b)['stock']==1,o)
s,o=order([line(a,1),{'product_id':2147483647,'quantity':1}])
check('Missing product rolls back order',s==404 and get(a)['stock']==10,o)
call('PATCH',f'/products/{a["id"]}',{'price':'15.00'})
s,o=call('GET',f'/orders/{oid}')
check('Historical order price snapshot',s==200 and o['total_amount']=='45.00' and next(x for x in o['items'] if x['product_id']==a['id'])['unit_price']=='12.50',o)
z=create('ZERO',stock=0,price='30.00')
eventually(lambda:search(category=f'qa{RUN}')[1].get('total')==3)
for name,params,predicate in [
 ('Category filter',{'category':f'qa{RUN}'},lambda h:h['total']==3),
 ('Price range filter',{'category':f'qa{RUN}','min_price':19,'max_price':21},lambda h:h['total']==1 and h['items'][0]['id']==b['id']),
 ('In-stock filter',{'category':f'qa{RUN}','in_stock':'true'},lambda h:h['total']==2 and all(x['stock']>0 for x in h['items'])),
 ('Pagination',{'category':f'qa{RUN}','limit':1,'offset':1},lambda h:h['total']==3 and len(h['items'])==1),
 ('No search matches',{'q':f'absentword{RUN}zz'},lambda h:h['total']==0 and not h['items'])]:
    s,h=search(**params); check(name,s==200 and predicate(h),h)
cases=[
 ('Missing product', 'GET','/products/2147483647',None,404),
 ('Missing order','GET','/orders/2147483647',None,404),
 ('Duplicate SKU','POST','/products',{'sku':a['sku'],'name':'duplicate','price':'1','stock':1},409),
 ('Zero order quantity','POST','/orders',{'items':[line(a,0)]},422),
 ('Negative order quantity','POST','/orders',{'items':[line(a,-1)]},422),
 ('Negative price','PATCH',f'/products/{a["id"]}',{'price':'-1'},422),
 ('Duplicate product lines','POST','/orders',{'items':[line(a,1),line(a,1)]},422),
 ('Empty order','POST','/orders',{'items':[]},422),
 ('Zero stock adjustment','POST',f'/products/{a["id"]}/stock',{'delta':0},422),
 ('Null product name','PATCH',f'/products/{a["id"]}',{'name':None},422),
]
for name,method,path,data,expected in cases:
    s,h=call(method,path,data); check(name,s==expected,{'status':s,'expected':expected,'body':h})
c=create('CONCURRENT',stock=40)
with ThreadPoolExecutor(max_workers=30) as pool:
    jobs=[pool.submit(order,[line(c,1)]) for _ in range(60)]
    jobs += [pool.submit(call,'POST',f'/products/{c["id"]}/stock',{'delta':2}) for _ in range(10)]
    rr=[f.result() for f in jobs]
sold=sum(s==201 for s,_ in rr[:60]); stock=get(c)['stock']
check('Concurrent warehouse writes and orders',all(s in (201,409) for s,_ in rr[:60]) and all(s==200 for s,_ in rr[60:]) and stock==40+20-sold,{'orders_created':sold,'order_rejections':sum(s==409 for s,_ in rr[:60]),'restocks':10,'stock':stock,'expected':60-sold})
Path(__file__).with_name('remaining-checks.json').write_text(json.dumps({'run':RUN,'products':[a['id'],b['id'],z['id'],c['id']],'results':results},indent=2))
print(f'FINAL: {sum(r["pass"] for r in results)}/{len(results)} passed')
