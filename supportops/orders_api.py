import json
import time
from pathlib import Path
from fastapi import FastAPI, HTTPException
from supportops.schemas import Order

app = FastAPI(title="Northstar Goods · synthetic orders API", version="0.2.0")
DATA = Path(__file__).resolve().parents[1] / "data/orders.json"


@app.get("/health/live")
def live():
    return {"status": "ok", "synthetic": True}


@app.get("/orders/{order_id}", response_model=Order)
def get_order(order_id: str):
    orders = json.loads(DATA.read_text())
    order = next((x for x in orders if x["id"] == order_id), None)
    if not order:
        raise HTTPException(404, "order_not_found")
    return order
