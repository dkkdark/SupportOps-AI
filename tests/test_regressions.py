import pytest
from supportops.workflow import classify


@pytest.mark.parametrize('message',['Срок возврата денег','Когда возврат денег?','Нужна проверка денежных списаний'])
def test_money_inflections_are_payment_intent(message):
    assert 'payment' in classify(message)
