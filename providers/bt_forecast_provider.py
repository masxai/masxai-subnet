from __future__ import annotations

"""BT-Forecast task provider with HMAC auth, cursors, ETags, and retries."""

import asyncio
import hashlib
import hmac
import random
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from config import Settings
from models import SyncState, as_utc, utcnow
from providers.base import ProviderTask


class BTForecastTaskProvider:
    def __init__(self, settings: Settings, session_factory):
        self.settings = settings
        self.session_factory = session_factory
        self._semaphore = asyncio.Semaphore(settings.bt_forecast_max_concurrent_requests)

    async def fetch_tasks(self) -> list[ProviderTask]:
        tasks: list[ProviderTask] = []
        cursor = self._read_state("bt_forecast:cursor").value
        has_more = True

        async with httpx.AsyncClient(
            base_url=self.settings.bt_forecast_base_url,
            timeout=self.settings.bt_forecast_request_timeout_seconds,
        ) as client:
            while has_more:
                page, next_cursor, has_more, not_modified = await self._fetch_page(
                    client=client,
                    cursor=cursor,
                    limit=self.settings.bt_forecast_page_size,
                )
                if not_modified:
                    break
                tasks.extend(page)
                cursor = next_cursor
                if next_cursor:
                    self._write_state("bt_forecast:cursor", next_cursor)
                if not has_more:
                    break
        return tasks

    async def _fetch_page(
        self, client: httpx.AsyncClient, cursor: Optional[str], limit: int
    ) -> tuple[list[ProviderTask], Optional[str], bool, bool]:
        query = {"limit": str(limit)}
        if cursor:
            query["since"] = cursor
        path = f"/v1/tasks?{urlencode(query)}"
        etag_key = f"bt_forecast:etag:{cursor or 'initial'}"
        etag = self._read_state(etag_key).etag

        attempt = 0
        while True:
            attempt += 1
            headers = self._signed_headers("GET", path, b"")
            if etag:
                headers["If-None-Match"] = etag

            async with self._semaphore:
                response = await client.get(path, headers=headers)

            if response.status_code == 304:
                return [], cursor, False, True
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt >= self.settings.bt_forecast_max_retries:
                    response.raise_for_status()
                await self._sleep_for_retry(response, attempt)
                continue
            response.raise_for_status()

            response_etag = response.headers.get("ETag")
            if response_etag:
                self._write_state(etag_key, value=None, etag=response_etag)
            payload = response.json()
            return (
                [self._normalize_task(item) for item in payload.get("tasks", [])],
                payload.get("next_cursor"),
                bool(payload.get("has_more", False)),
                False,
            )

    def _signed_headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        timestamp = str(int(time.time()))
        body_sha = hashlib.sha256(body).hexdigest()
        canonical = f"{method.upper()}\n{path}\n{timestamp}\n{body_sha}"
        signature = hmac.new(
            self.settings.bt_forecast_api_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-API-Key": self.settings.bt_forecast_api_key,
            "X-Timestamp": timestamp,
            "X-Signature": signature,
        }

    async def _sleep_for_retry(self, response: httpx.Response, attempt: int) -> None:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                await asyncio.sleep(float(retry_after))
                return
            except ValueError:
                pass
        base = min(30.0, 0.5 * (2 ** (attempt - 1)))
        await asyncio.sleep(base + random.uniform(0.0, 0.25))

    def _normalize_task(self, item: dict[str, Any]) -> ProviderTask:
        return ProviderTask(
            task_id=str(item["task_id"]),
            question=str(item["question"]),
            category=str(item.get("category") or "general"),
            deadline=as_utc(item["deadline"]),
            resolution_hint=str(item.get("resolution_hint") or ""),
            source="bt_forecast",
            schema_version=str(item.get("schema_version") or "1.0"),
            created_at=as_utc(item.get("created_at")) or utcnow(),
            updated_at=as_utc(item.get("updated_at")) or utcnow(),
        )

    def _read_state(self, key: str) -> SyncState:
        with self.session_factory() as session:
            state = session.get(SyncState, key)
            if state is None:
                return SyncState(key=key, value=None, etag=None, updated_at=utcnow())
            return state

    def _write_state(
        self, key: str, value: Optional[str] = None, etag: Optional[str] = None
    ) -> None:
        with self.session_factory() as session:
            state = session.get(SyncState, key)
            if state is None:
                state = SyncState(key=key)
                session.add(state)
            state.value = value
            if etag is not None:
                state.etag = etag
            state.updated_at = utcnow()
            session.commit()
