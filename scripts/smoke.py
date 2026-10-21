import argparse
import os
import uuid
import httpx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',default='http://127.0.0.1:8010')
    args=parser.parse_args()
    with httpx.Client(base_url=args.url,headers={'X-API-Key':os.environ.get('APP_API_KEY','')},timeout=40) as c:
        c.get('/health/ready').raise_for_status()
        t=c.post('/api/tickets',headers={'Idempotency-Key':uuid.uuid4().hex},json={'customer':'Анна Смирнова','subject':'Smoke: статус и отмена','message':'Заказ 1042 должен был прийти вчера. Где он и можно ли отменить покупку?','order_id':'1042'}).json()
        r=c.post(f"/api/tickets/{t['id']}/analyze",json={})
        r.raise_for_status()
        t=r.json()
        assert t['status']=='review',t
        assert 'отмена до отправки недоступна' in t['result']['draft']
        assert t['result']['sources']
        repeated=c.post(f"/api/tickets/{t['id']}/analyze",json={}).json()
        assert repeated['result']['trace_id']==t['result']['trace_id']
        r=c.post(f"/api/tickets/{t['id']}/review",json={'decision':'approve','text':t['result']['draft'],'revision':t['revision']})
        r.raise_for_status()
        assert r.json()['status']=='approved'
        print('PASS: health → create → analyze → sources → idempotency → review')


if __name__=='__main__':main()
