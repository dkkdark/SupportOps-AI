from datetime import date
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Category = Literal["delivery", "cancellation", "returns", "damage", "payment", "other"]


class TicketInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer: str = Field(min_length=2, max_length=100)
    email: str = Field(
        default="", max_length=160, pattern=r"^$|^[^\s@]+@[^\s@]+\.[^\s@]+$"
    )
    subject: str = Field(min_length=3, max_length=180)
    message: str = Field(min_length=10, max_length=5000)
    order_id: str | None = Field(default=None, pattern=r"^\d{4,12}$")
    channel: Literal["email", "chat", "web", "api", "internal"] = "email"
    priority: Literal["normal", "high"] = "normal"
    request_type_id: str | None = Field(default=None, max_length=40)
    assigned_to: str | None = Field(default=None, max_length=40)
    template_id: str | None = Field(default=None, max_length=40)


class Order(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    customer: str
    status: Literal["processing", "shipped", "delayed", "delivered", "cancelled"]
    total: float = Field(ge=0)
    currency: Literal["RUB"] = "RUB"
    placed_at: date
    expected_at: date
    delivered_at: date | None = None
    carrier: str
    tracking: str | None = None
    items: list[dict]


class AnalyzeInput(BaseModel):
    fault: Literal["none", "orders_timeout", "invalid_model", "missing_sources"] = (
        "none"
    )


class ReviewInput(BaseModel):
    decision: Literal["approve", "escalate"]
    text: str = Field(default="", max_length=10000)
    note: str = Field(default="", max_length=1000)
    revision: int = Field(ge=0)
    send: bool = False
    manual: bool = False


class ModelSelection(BaseModel):
    """The model selects evidence. Facts and actions are rendered by trusted code."""

    model_config = ConfigDict(extra="forbid")
    fact_ids: list[str] = Field(min_length=1, max_length=12)
    tone: Literal["empathetic", "neutral"]


class Rule(BaseModel):
    id: str
    key: str
    title: str
    category: Category
    version: str
    effective_from: str
    effective_to: str | None = None
    text: str
    tags: list[str]
    facts: dict[str, str]
