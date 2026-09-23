"""Fire-and-forget dispatch to n8n. Never awaited by the request handler."""
from __future__ import annotations

import asyncio
import logging

import httpx

from config import settings
from schemas import SlowPathEvent

log = logging.getLogger("slow_path")

_client: httpx.AsyncClient | None = None
_tasks: set[asyncio.Task] = set()


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=settings.n8n_timeout_s)
    return _client


async def _post(event: SlowPathEvent) -> None:
    try:
        r = await _get_client().post(settings.n8n_webhook_url, json=event.model_dump())
        log.info("n8n %s call=%s status=%s", event.event_type, event.call_id, r.status_code)
    except Exception as exc:  # noqa: BLE001 - slow path must never raise into fast path
        log.warning("n8n dispatch failed call=%s: %s", event.call_id, exc)


def dispatch(event: SlowPathEvent) -> None:
    task = asyncio.create_task(_post(event))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def shutdown() -> None:
    global _client
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
    if _client is not None:
        await _client.aclose()
        _client = None
