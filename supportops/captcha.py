"""Turnstile verification for public entry points; tokens are never logged."""

from urllib.parse import urlparse
import httpx
from fastapi import HTTPException


def verify(config, token, request, mandatory=True):
    if not config.turnstile_secret_key:
        if mandatory:
            raise HTTPException(
                503,
                "The service operator must configure public signup spam protection.",
            )
        return
    if not token:
        raise HTTPException(422, "Complete the spam-protection check.")
    try:
        response = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": config.turnstile_secret_key,
                "response": token,
                "remoteip": request.client.host,
            },
            timeout=5,
        )
        result = response.json() if response.is_success else {}
        if not result.get("success") or (
            config.app_env == "production"
            and result.get("hostname") != urlparse(config.public_base_url).hostname
        ):
            raise ValueError()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(422, "Spam-protection check failed. Try again.") from None
