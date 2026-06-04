"""Aliyun NLS CreateToken — handcrafted RPC v1 signature, no aliyun-python-sdk-core.

CreateToken is a standard Aliyun RPC-style API at nls-meta.<region>.aliyuncs.com.
Signing algorithm reference:
  https://help.aliyun.com/zh/sdk/product-overview/rpc-mechanism

We cache the token in memory and refresh ~5 min before its ExpireTime.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import time
import uuid
from urllib.parse import quote

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def _percent(s: str) -> str:
    # Aliyun signing requires RFC 3986 percent-encoding; quote() with safe=""
    # encodes /+= etc. correctly. Spaces become %20, not +.
    return quote(str(s), safe="~")


def _sign(method: str, params: dict, ak_secret: str) -> str:
    sorted_qs = "&".join(f"{_percent(k)}={_percent(v)}" for k, v in sorted(params.items()))
    string_to_sign = f"{method}&{_percent('/')}&{_percent(sorted_qs)}"
    digest = hmac.new(
        (ak_secret + "&").encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


class TokenCache:
    def __init__(self) -> None:
        self._token: str = ""
        self._expires_at: float = 0.0  # unix seconds
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=10.0)

    async def get(self) -> str:
        # Refresh 5 min early to avoid edge expiry mid-call.
        if self._token and time.time() < self._expires_at - 300:
            return self._token

        async with self._lock:
            if self._token and time.time() < self._expires_at - 300:
                return self._token
            await self._refresh()
            return self._token

    async def _refresh(self) -> None:
        if not settings.aliyun_ak_id or not settings.aliyun_ak_secret:
            raise RuntimeError("ALIYUN_AK_ID / ALIYUN_AK_SECRET not configured")

        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        params = {
            "AccessKeyId": settings.aliyun_ak_id,
            "Action": "CreateToken",
            "Format": "JSON",
            "RegionId": settings.aliyun_nls_region,
            "SignatureMethod": "HMAC-SHA1",
            "SignatureNonce": str(uuid.uuid4()),
            "SignatureVersion": "1.0",
            "Timestamp": ts,
            "Version": "2019-02-28",
        }
        params["Signature"] = _sign("GET", params, settings.aliyun_ak_secret)

        url = f"https://nls-meta.{settings.aliyun_nls_region}.aliyuncs.com/"
        resp = await self._client.get(url, params=params)
        resp.raise_for_status()
        body = resp.json()
        if "Token" not in body:
            raise RuntimeError(f"CreateToken failed: {body}")

        self._token = body["Token"]["Id"]
        self._expires_at = float(body["Token"]["ExpireTime"])
        logger.info(
            "NLS token refreshed, expires in %.0f min",
            (self._expires_at - time.time()) / 60,
        )

    async def close(self) -> None:
        await self._client.aclose()


token_cache = TokenCache()
