"""Offline Qdrant HTTP contract fixture. CI also exercises the real server."""

import copy
import json
import math
import httpx


class QdrantService:
    def __init__(self):
        self.collections = {}
        self.calls = []
        self.unavailable = False
        self.unconfirmed = False
        self.inject = []

    @staticmethod
    def matches(point, scope):
        for condition in scope.get("must", []):
            value = point.get("payload", {}).get(condition["key"])
            match = condition["match"]
            if "value" in match and value != match["value"]:
                return False
            if "any" in match and value not in match["any"]:
                return False
        return True

    def __call__(self, request):
        body = json.loads(request.content) if request.content else {}
        path = request.url.path
        self.calls.append((request.method, path, copy.deepcopy(body)))
        if self.unavailable:
            return httpx.Response(503, json={"private": "provider error"})
        if path == "/readyz":
            return httpx.Response(200, text="ready")
        name = path.split("/")[2]
        suffix = "/".join(path.split("/")[3:])
        if not suffix and request.method == "PUT":
            if name in self.collections:
                return httpx.Response(409)
            self.collections[name] = {
                "config": {"params": body},
                "payload_schema": {},
                "points": {},
            }
            result = True
        elif name not in self.collections:
            return httpx.Response(404, json={"status": {"error": "not found"}})
        else:
            col = self.collections[name]
            if not suffix:
                result = {k: v for k, v in col.items() if k != "points"}
            elif suffix == "index":
                col["payload_schema"][body["field_name"]] = {"data_type": "keyword"}
                result = {"status": "completed"}
            elif suffix == "points" and request.method == "PUT":
                col["points"].update(
                    {p["id"]: copy.deepcopy(p) for p in body["points"]}
                )
                result = {"status": "acknowledged" if self.unconfirmed else "completed"}
            elif suffix == "points":
                result = [col["points"][i] for i in body["ids"] if i in col["points"]]
            elif suffix == "points/count":
                result = {
                    "count": sum(
                        self.matches(p, body["filter"]) for p in col["points"].values()
                    )
                }
            elif suffix == "points/query":
                q = body["query"]
                result = []
                for p in col["points"].values():
                    if not self.matches(p, body["filter"]):
                        continue
                    v = p["vector"]
                    score = sum(a * b for a, b in zip(q, v)) / math.sqrt(
                        sum(a * a for a in q) * sum(b * b for b in v)
                    )
                    if score >= body.get("score_threshold", -1):
                        result.append({**p, "score": score})
                result = {
                    "points": sorted(result, key=lambda p: -p["score"])[: body["limit"]]
                    + self.inject
                }
            else:
                raise AssertionError((request.method, path))
        return httpx.Response(200, json={"status": "ok", "result": result})
