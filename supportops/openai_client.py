"""Bounded OpenAI HTTP calls. No credentials or provider payloads in exceptions/logs."""

import math
import time
import httpx


class ModelError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class OpenAIClient:
    def __init__(self, config, transport=None, deadline=None):
        self.uncertain = False
        self.config = config
        self.deadline = deadline or time.monotonic() + config.agent_timeout_seconds
        key = config.openai_api_key or config.llm_api_key
        if not key:
            raise ModelError("model_not_configured")
        self.client = httpx.Client(
            base_url=config.openai_base_url.rstrip("/") + "/",
            headers={"Authorization": "Bearer " + key},
            transport=transport,
            timeout=25,
        )
        self.usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "embedding_tokens": 0,
            "cost_usd": 0.0,
            "attempts": 0,
        }

    def close(self):
        self.client.close()

    def post(self, path, payload, *, embedding=False):
        remaining = self.deadline - time.monotonic()
        if remaining <= 1:
            raise ModelError("agent_budget_exceeded")
        import json

        input_rate = (
            self.config.verifier_input_usd_per_million
            if payload.get("model") == self.config.verifier_model
            else self.config.model_input_usd_per_million
        )
        output_rate = (
            self.config.verifier_output_usd_per_million
            if payload.get("model") == self.config.verifier_model
            else self.config.model_output_usd_per_million
        )
        input_upper = len(json.dumps(payload, ensure_ascii=False).encode())
        output_upper = 0 if embedding else payload.get("max_output_tokens", 0)
        upper_cost = (
            input_upper
            * (self.config.embedding_input_usd_per_million if embedding else input_rate)
            + output_upper * output_rate
        ) / 1e6
        if self.usage["cost_usd"] + upper_cost > self.config.agent_provider_cost_limit:
            raise ModelError("agent_budget_exceeded")
        self.usage["attempts"] += 1
        try:
            r = self.client.post(path, json=payload, timeout=min(30, remaining))
            if not r.is_success:
                if r.status_code >= 500:
                    self.uncertain = True
                raise ModelError(
                    "model_not_configured"
                    if r.status_code in (401, 403, 404)
                    else "invalid_model_request"
                    if r.status_code == 400
                    else "model_rate_limited"
                    if r.status_code == 429
                    else "model_unavailable"
                )
            if len(r.content) > 8 * 1024 * 1024:
                raise ModelError("invalid_model_output")
            body = r.json()
        except (httpx.HTTPError, ValueError):
            self.uncertain = True
            raise ModelError("model_unavailable") from None
        usage = body.get("usage", {})
        if embedding:
            n = int(usage.get("total_tokens", 0))
            self.usage["embedding_tokens"] += n
            self.usage["cost_usd"] += (
                n * self.config.embedding_input_usd_per_million / 1e6
            )
        else:
            n = int(usage.get("input_tokens", 0))
            out = int(usage.get("output_tokens", 0))
            self.usage["input_tokens"] += n
            self.usage["output_tokens"] += out
            self.usage["cost_usd"] += (n * input_rate + out * output_rate) / 1e6
        self.usage["cost_usd"] = round(self.usage["cost_usd"], 8)
        return body

    def embeddings(self, texts):
        vectors = []
        for start in range(0, len(texts), 32):
            batch = texts[start : start + 32]
            body = self.post(
                "embeddings",
                {
                    "model": self.config.embedding_model,
                    "input": batch,
                    "dimensions": 1536,
                    "encoding_format": "float",
                },
                embedding=True,
            )
            rows = sorted(body.get("data", []), key=lambda r: r.get("index", -1))
            if len(rows) != len(batch) or [r.get("index") for r in rows] != list(
                range(len(batch))
            ):
                raise ModelError("invalid_embedding_output")
            for row in rows:
                vector = row.get("embedding", [])
                if len(vector) != 1536 or any(
                    not isinstance(x, (int, float)) or not math.isfinite(x)
                    for x in vector
                ):
                    raise ModelError("invalid_embedding_output")
                norm = math.sqrt(sum(x * x for x in vector))
                if norm < 1e-10:
                    raise ModelError("invalid_embedding_output")
                vectors.append([x / norm for x in vector])
        return vectors

    def response(
        self, *, model, instructions, inputs, schema, tools=None, tool_choice="auto"
    ):
        payload = {
            "model": model,
            "instructions": instructions,
            "input": inputs,
            "store": False,
            "max_output_tokens": self.config.agent_max_output_tokens,
            "reasoning": {"effort": "low"},
            "include": ["reasoning.encrypted_content"],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "supportops_result",
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if tools:
            payload.update(
                tools=tools, tool_choice=tool_choice, parallel_tool_calls=False
            )
        body = self.post("responses", payload)
        if body.get("status") != "completed":
            raise ModelError("invalid_model_output")
        return body


def output_json(body):
    import json

    for item in body.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "refusal":
                    raise ModelError("model_refused")
                if part.get("type") == "output_text":
                    try:
                        return json.loads(part["text"])
                    except (ValueError, KeyError):
                        raise ModelError("invalid_model_output") from None
    raise ModelError("invalid_model_output")
