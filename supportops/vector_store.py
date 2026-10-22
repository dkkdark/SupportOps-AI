"""Small Qdrant REST adapter. No model calls, plaintext documents or public API proxy."""

import math
from urllib.parse import urlsplit
from uuid import UUID, uuid5
import httpx
from supportops.openai_client import ModelError

DIMENSIONS = 1536
POINT_NAMESPACE = UUID("367ce38d-9396-49c9-aa96-736c8dffcc81")


def point_id(org_id, document_id, profile, text_hash):
    return str(uuid5(POINT_NAMESPACE, f"{org_id}:{document_id}:{profile}:{text_hash}"))


def valid_vector(vector):
    return (
        isinstance(vector, list)
        and len(vector) == DIMENSIONS
        and all(
            isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)
            for x in vector
        )
        and math.isfinite(sum(x * x for x in vector))
        and sum(x * x for x in vector) > 0
    )


class QdrantStore:
    def __init__(self, config, profile, transport=None):
        url = urlsplit(config.qdrant_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ModelError("vector_store_not_configured")
        # Secrets may use HTTP only over a trusted local/container network.
        if (
            config.qdrant_api_key
            and url.scheme != "https"
            and url.hostname not in ("localhost", "127.0.0.1", "::1", "qdrant")
        ):
            raise ModelError("vector_store_tls_required")
        self.collection = f"{config.qdrant_collection}_{profile[:24]}"
        self.path = f"/collections/{self.collection}"
        self.client = httpx.Client(
            base_url=config.qdrant_url.rstrip("/"),
            headers={"api-key": config.qdrant_api_key} if config.qdrant_api_key else {},
            timeout=config.qdrant_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def close(self):
        self.client.close()

    def _request(
        self, method, path, body=None, allow_missing=False, allow_conflict=False
    ):
        try:
            response = self.client.request(method, path, json=body)
            if allow_missing and response.status_code == 404:
                return None
            if allow_conflict and response.status_code == 409:
                return None
            if response.status_code in (401, 403):
                raise ModelError("vector_store_auth_failed")
            if not response.is_success:
                raise ModelError("vector_store_unavailable")
            data = response.json()
            if data.get("status") != "ok" or "result" not in data:
                raise ModelError("vector_store_invalid_response")
            return data["result"]
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            # Never expose a URL, API key, provider body or business payload in errors.
            raise ModelError("vector_store_unavailable") from None

    def ensure(self):
        result = self._request("GET", self.path, allow_missing=True)
        if result is None:
            self._request(
                "PUT",
                self.path,
                {
                    "vectors": {
                        "size": DIMENSIONS,
                        "distance": "Cosine",
                        "on_disk": True,
                    },
                    "on_disk_payload": True,
                },
                allow_conflict=True,
            )
            result = self._request("GET", self.path)
        try:
            vectors = result["config"]["params"]["vectors"]
            if vectors["size"] != DIMENSIONS or vectors["distance"] != "Cosine":
                raise ModelError("vector_store_profile_mismatch")
        except (KeyError, TypeError):
            raise ModelError("vector_store_invalid_response") from None
        for key in ("organization_id", "document_id", "profile"):
            schema = result.get("payload_schema", {}).get(key, {})
            if schema.get("data_type") == "keyword":
                continue
            self._request(
                "PUT",
                self.path + "/index?wait=true",
                {
                    "field_name": key,
                    "field_schema": {"type": "keyword", "is_tenant": True}
                    if key == "organization_id"
                    else "keyword",
                },
            )

    @staticmethod
    def scoped_filter(org_id, profile, document_ids):
        if not org_id or not profile or not document_ids:
            raise ModelError("invalid_vector_scope")
        return {
            "must": [
                {"key": "organization_id", "match": {"value": org_id}},
                {"key": "profile", "match": {"value": profile}},
                {"key": "document_id", "match": {"any": list(document_ids)}},
            ]
        }

    def retrieve(self, ids):
        rows = []
        for start in range(0, len(ids), 128):
            result = self._request(
                "POST",
                self.path + "/points",
                {
                    "ids": ids[start : start + 128],
                    "with_payload": True,
                    "with_vector": True,
                },
            )
            if not isinstance(result, list):
                raise ModelError("vector_store_invalid_response")
            rows.extend(result)
        return rows

    def upsert(self, points):
        for start in range(0, len(points), 64):
            batch = points[start : start + 64]
            if not all(valid_vector(p.get("vector")) for p in batch):
                raise ModelError("invalid_embeddings")
            result = self._request(
                "PUT", self.path + "/points?wait=true", {"points": batch}
            )
            if not isinstance(result, dict) or result.get("status") != "completed":
                raise ModelError("vector_store_write_unconfirmed")

    def count(self, scope):
        result = self._request(
            "POST",
            self.path + "/points/count",
            {
                "filter": scope,
                "exact": True,
            },
            allow_missing=True,
        )
        if result is None:
            raise ModelError("vector_index_missing")
        if not isinstance(result, dict) or not isinstance(result.get("count"), int):
            raise ModelError("vector_store_invalid_response")
        return result["count"]

    def query(self, vector, scope, limit=60):
        if not valid_vector(vector):
            raise ModelError("invalid_embeddings")
        result = self._request(
            "POST",
            self.path + "/points/query",
            {
                "query": vector,
                "filter": scope,
                "limit": limit,
                "with_payload": True,
                "with_vector": True,
                "score_threshold": 0.20,
            },
        )
        if not isinstance(result, dict) or not isinstance(result.get("points"), list):
            raise ModelError("vector_store_invalid_response")
        return result["points"]

    def health(self):
        try:
            response = self.client.get("/readyz")
            return response.status_code == 200
        except httpx.HTTPError:
            return False
