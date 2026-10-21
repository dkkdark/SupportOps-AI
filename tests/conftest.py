import copy
import json
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from supportops.config import Settings
from supportops.main import create_app


@pytest.fixture
def orders():
    return json.loads(Path("data/orders.json").read_text())


def orders_handler(orders):
    def handler(request):
        if request.url.path == "/health/live":
            return httpx.Response(200,json={"status":"ok"})
        order = next((o for o in orders if request.url.path == "/orders/"+o["id"]),None)
        return httpx.Response(200 if order else 404,json=copy.deepcopy(order) if order else {"detail":"not_found"})
    return handler


@pytest.fixture
def client(tmp_path,orders):
    app = create_app(Settings(database_url=f"sqlite:///{tmp_path}/test.db",fault_injection_enabled=True),orders_transport=httpx.MockTransport(orders_handler(orders)))
    with TestClient(app) as c:
        yield c


@pytest.fixture
def payload():
    return {"customer":"Анна Смирнова","email":"customer1@example.test","subject":"Вопрос по заказу","message":"Где заказ 1042 и можно ли отменить покупку?","order_id":"1042","channel":"web"}
