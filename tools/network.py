from __future__ import annotations

from typing import Any

import httpx

from config import settings


async def get(url: str) -> str:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text[:12000]


async def post(url: str, payload: dict[str, Any]) -> str:
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.text[:12000]
