import pytest
from supportops.workflow import classify


@pytest.mark.parametrize('message',['Срок возврата денег','Когда возврат денег?','Нужна проверка денежных списаний'])
def test_money_inflections_are_payment_intent(message):
    assert 'payment' in classify(message)


def test_infers_order_from_message_and_rejects_mismatch(client,payload):
    t=client.post('/api/tickets',json={**payload,'order_id':None}).json()
    r=client.post(f"/api/tickets/{t['id']}/analyze",json={}).json()
    assert r['status']=='review' and r['order_id']=='1042'
    t=client.post('/api/tickets',json={**payload,'order_id':'1043'}).json()
    r=client.post(f"/api/tickets/{t['id']}/analyze",json={}).json()
    assert r['result']['reason']=='order_id_mismatch'
