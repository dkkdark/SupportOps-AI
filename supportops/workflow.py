import hashlib
import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import TypedDict
import httpx
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from opentelemetry import trace
from pydantic import ValidationError
from supportops.retrieval import retrieve, serialize_document, EMBEDDING_VERSION
from supportops.schemas import ModelSelection, Order
from supportops.telemetry import step

PROMPT = (Path(__file__).parent / "prompts/selection-v1.txt").read_text()
PROMPT_VERSION = "selection-v1-" + hashlib.sha256(PROMPT.encode()).hexdigest()[:12]
STATUS = {
    "processing": "is being prepared at the warehouse",
    "shipped": "has been handed to the carrier",
    "delayed": "is delayed in transit",
    "delivered": "has been delivered",
    "cancelled": "has been cancelled",
}


class State(TypedDict, total=False):
    ticket_id: str
    message: str
    customer: str
    order_id: str | None
    fault: str
    trace_id: str
    categories: list[str]
    order: dict | None
    sources: list[dict]
    facts: dict[str, str]
    required: list[str]
    draft: str | None
    reason: str | None
    checks: list[dict]
    steps: list[dict]
    selection: dict
    usage: dict
    manifest: dict
    review: dict


def classify(message):
    text = message.lower().replace("ё", "е")
    groups = {
        "delivery": [
            "достав",
            "где",
            "вчера",
            "приш",
            "трек",
            "пути",
            "delivery",
            "deliver",
            "where",
            "yesterday",
            "arriv",
            "tracking",
            "in transit",
        ],
        "cancellation": ["отмен", "передум", "отказ", "cancel", "changed my mind"],
        "returns": [
            "вернуть",
            "возврат",
            "не подош",
            "return",
            "does not fit",
            "doesn't fit",
            "refund",
        ],
        "damage": [
            "разбит",
            "поврежд",
            "трещин",
            "брак",
            "сломан",
            "broken",
            "damag",
            "crack",
            "defect",
        ],
        "payment": [
            "деньг",
            "денег",
            "денеж",
            "оплат",
            "спис",
            "платеж",
            "money",
            "payment",
            "paid",
            "charged",
            "refund",
            "card",
        ],
    }
    cats = [cat for cat, tokens in groups.items() if any(t in text for t in tokens)]
    return cats or ["other"]


def build_graph(
    settings, sessions, checkpointer, orders_transport=None, llm_transport=None
):
    def classification(s):
        with step(s, "classify"):
            s["categories"] = classify(s["message"])
            mentioned = set(
                re.findall(
                    r"(?:заказ\w*\s*(?:№|#)?\s*|order\s*(?:number|no\.?|#)?\s*|[№#]\s*)(\d{4,12})",
                    s["message"],
                    re.I,
                )
            )
            if len(mentioned) > 1 or (
                mentioned and s.get("order_id") and s["order_id"] not in mentioned
            ):
                s["reason"] = "order_id_mismatch"
            elif len(mentioned) == 1 and not s.get("order_id"):
                s["order_id"] = next(iter(mentioned))
            if re.search(
                r"ignore.{0,30}instructions|system prompt|игнорир\w*.{0,30}инструк|забудь.{0,20}правил",
                s["message"],
                re.I,
            ):
                s["reason"] = "untrusted_instructions"
        return s

    def order_lookup(s):
        with step(s, "orders_api"):
            if s.get("reason"):
                return s
            if not s.get("order_id"):
                s["reason"] = "missing_order_id"
                return s
            try:
                if s.get("fault") == "orders_timeout":
                    raise httpx.ReadTimeout("injected")
                with httpx.Client(
                    timeout=settings.orders_timeout_seconds, transport=orders_transport
                ) as client:
                    r = client.get(f"{settings.orders_api_url}/orders/{s['order_id']}")
                if r.status_code == 404:
                    s["reason"] = "order_not_found"
                else:
                    r.raise_for_status()
                    order = Order.model_validate(r.json())
                    if order.id != s["order_id"]:
                        s["reason"] = "invalid_order_data"
                    elif (
                        order.customer.casefold().strip()
                        != s["customer"].casefold().strip()
                    ):
                        s["reason"] = "ownership_review"
                    else:
                        s["order"] = order.model_dump(mode="json")
            except (httpx.TimeoutException, httpx.ConnectError):
                s["reason"] = "orders_unavailable"
            except (httpx.HTTPError, ValidationError, ValueError):
                s["reason"] = "invalid_order_data"
        return s

    def retrieval(s):
        with step(s, "retrieval"):
            if s.get("reason"):
                return s
            try:
                with sessions() as db:
                    docs = retrieve(db, s["message"], s["categories"])
                s["sources"] = [serialize_document(d) for d in docs]
                if s.get("fault") == "missing_sources":
                    s["sources"] = []
                if not s["sources"]:
                    s["reason"] = "missing_sources"
                    return s
                order = s["order"]
                source_facts = {
                    k: v for d in s["sources"] for k, v in d["facts"].items()
                }
                facts = {
                    "order_status": f"According to the store, order #{order['id']} {STATUS[order['status']]}."
                }
                if order.get("tracking"):
                    facts["tracking"] = (
                        f"Carrier: {order['carrier']}. Tracking number: {order['tracking']}."
                    )
                required = ["order_status"]
                needed = []
                if "delivery" in s["categories"]:
                    needed += (
                        ["delay_policy"]
                        if order["status"] == "delayed"
                        else ["delivery_window"]
                    )
                    if "tracking" in facts:
                        required += ["tracking"]
                if "cancellation" in s["categories"]:
                    if order["status"] in ("shipped", "delayed"):
                        needed += ["cancel_shipped"]
                    elif order["status"] == "processing":
                        needed += ["cancel_processing"]
                    else:
                        s["reason"] = "action_requires_review"
                if "returns" in s["categories"]:
                    needed += ["return_window", "return_shipping"]
                    window = re.search(
                        r"(?:within|течение)\s+(\d+)\s+(?:calendar|календарных)",
                        source_facts.get("return_window", ""),
                        re.I,
                    )
                    days = int(window[1]) if window else None
                    if days is None or not 1 <= days <= 365:
                        s["reason"] = "missing_sources"
                    elif (
                        not order.get("delivered_at")
                        or date.fromisoformat(order["delivered_at"]) > date.today()
                        or date.today()
                        > date.fromisoformat(order["delivered_at"])
                        + timedelta(days=days)
                    ):
                        s["reason"] = "return_eligibility_review"
                if "damage" in s["categories"]:
                    s["reason"] = "damage_requires_review"
                if "payment" in s["categories"]:
                    needed += (
                        ["refund_policy"]
                        if any(w in s["message"].lower() for w in ("возврат", "refund"))
                        else ["payment_policy"]
                    )
                    s["reason"] = "payment_requires_review"
                if any(
                    w in s["message"].lower()
                    for w in (
                        "адрес",
                        "сертификат",
                        "индивидуаль",
                        "address",
                        "gift card",
                        "custom-made",
                    )
                ):
                    s["reason"] = "action_requires_review"
                if s["categories"] == ["other"]:
                    s["reason"] = "unsupported_intent"
                for fact_id in needed:
                    if fact_id not in source_facts:
                        s["reason"] = "missing_sources"
                    else:
                        facts[fact_id] = source_facts[fact_id]
                        required.append(fact_id)
                s["facts"], s["required"] = facts, required
            except ValueError:
                s["reason"] = "conflicting_documents"
        return s

    def generation(s):
        with step(s, "generate"):
            selection = None
            usage = {
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "attempts": 0,
            }
            for attempt in range(2):
                usage["attempts"] += 1
                try:
                    if s.get("fault") == "invalid_model":
                        raw = {"fact_ids": ["invented_refund"], "tone": "neutral"}
                    elif settings.model_provider == "baseline":
                        raw = {"fact_ids": s["required"], "tone": "empathetic"}
                    elif settings.model_provider == "openai_compatible":
                        if not settings.llm_api_key or not settings.llm_model:
                            s["reason"] = "model_not_configured"
                            break
                        with httpx.Client(
                            timeout=20, transport=llm_transport
                        ) as client:
                            response = client.post(
                                settings.llm_base_url.rstrip("/") + "/chat/completions",
                                headers={
                                    "Authorization": f"Bearer {settings.llm_api_key}"
                                },
                                json={
                                    "model": settings.llm_model,
                                    "messages": [
                                        {"role": "system", "content": PROMPT},
                                        {
                                            "role": "user",
                                            "content": json.dumps(
                                                {
                                                    "customer_message": s["message"],
                                                    "evidence": s["facts"],
                                                    "required_fact_ids": s["required"],
                                                },
                                                ensure_ascii=False,
                                            ),
                                        },
                                    ],
                                    "response_format": {"type": "json_object"},
                                    "temperature": 0,
                                },
                            )
                            response.raise_for_status()
                            body = response.json()
                        u = body.get("usage", {})
                        usage["input_tokens"] += u.get("prompt_tokens", 0)
                        usage["output_tokens"] += u.get("completion_tokens", 0)
                        raw = json.loads(body["choices"][0]["message"]["content"])
                    else:
                        s["reason"] = "model_not_configured"
                        break
                    parsed = ModelSelection.model_validate(raw)
                    if not set(parsed.fact_ids) <= set(s["facts"]) or not set(
                        s["required"]
                    ) <= set(parsed.fact_ids):
                        raise ValueError("unsupported_or_missing_facts")
                    selection = parsed
                    break
                except (ValueError, KeyError, IndexError, ValidationError):
                    s["reason"] = "invalid_model_output"
                except (httpx.HTTPError, TypeError):
                    s["reason"] = "model_unavailable"
            usage["cost_usd"] = round(
                (
                    usage["input_tokens"] * settings.model_input_usd_per_million
                    + usage["output_tokens"] * settings.model_output_usd_per_million
                )
                / 1e6,
                8,
            )
            s["usage"] = usage
            if selection:
                s["reason"] = None
                s["selection"] = selection.model_dump()
                intro = (
                    "Hello! We understand that waiting for your order can be frustrating."
                    if selection.tone == "empathetic"
                    else "Hello! We have checked the details of your request."
                )
                s["draft"] = (
                    intro
                    + "\n\n"
                    + "\n\n".join(
                        s["facts"][k] for k in dict.fromkeys(selection.fact_ids)
                    )
                    + "\n\nPlease let us know if you need help with the next steps.\nWarm regards, the Listva team"
                )
        return s

    def validation(s):
        with step(s, "validate"):
            ids = set(s.get("selection", {}).get("fact_ids", []))
            s["checks"] = [
                {
                    "label": "Order details verified against the API",
                    "passed": bool(s.get("order")),
                },
                {"label": "Current policies applied", "passed": bool(s.get("sources"))},
                {
                    "label": "All required facts included",
                    "passed": bool(s.get("draft"))
                    and set(s.get("required", [])) <= ids,
                },
                {
                    "label": "No unsupported actions",
                    "passed": bool(ids) and ids <= set(s.get("facts", {})),
                },
            ]
            if not all(c["passed"] for c in s["checks"]) and not s.get("reason"):
                s["reason"] = "validation_failed"
                s["draft"] = None
            if s.get("draft"):
                used_ids = set(s.get("selection", {}).get("fact_ids", []))
                s["sources"] = [
                    d for d in s["sources"] if used_ids.intersection(d["facts"])
                ]
            s["manifest"] = {
                "code": settings.code_version,
                "prompt": PROMPT_VERSION,
                "model": settings.llm_model
                if settings.model_provider != "baseline"
                else "deterministic-baseline-v1",
                "documents": {d["id"]: d["version"] for d in s.get("sources", [])},
                "embedding": EMBEDDING_VERSION,
                "evaluation": "supportops-eval-v2-en",
            }
        return s

    def review(s):
        decision = interrupt(
            {
                "ticket_id": s["ticket_id"],
                "reason": s.get("reason"),
                "draft": s.get("draft"),
            }
        )
        return {"review": decision}

    graph = StateGraph(State)
    for name, fn in (
        ("classify", classification),
        ("orders_api", order_lookup),
        ("retrieval", retrieval),
        ("generate", generation),
        ("validate", validation),
        ("review", review),
    ):
        graph.add_node(name, fn)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "orders_api")
    graph.add_edge("orders_api", "retrieval")
    graph.add_conditional_edges(
        "retrieval", lambda s: "validate" if s.get("reason") else "generate"
    )
    graph.add_edge("generate", "validate")
    graph.add_edge("validate", "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)
