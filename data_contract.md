# BT-Forecast Task Contract

This is the handoff contract for a BT-Forecast backend serving many validator
processes concurrently.

## Authentication

Every request includes:

- `X-API-Key`: validator API key id
- `X-Timestamp`: unix seconds
- `X-Signature`: `HMAC_SHA256(api_secret, "{METHOD}\n{PATH}\n{TIMESTAMP}\n{SHA256_HEX(BODY)}")`

The server validates the signature and rejects requests when
`abs(now - timestamp) > 300s`.

## Endpoint

`GET /v1/tasks?since=<cursor>&limit=<n>`

- `since` is an opaque cursor from the previous response.
- `limit` defaults to `100` and must not exceed `500`.
- Cursor pagination is required. Do not implement offset or page-number
  pagination because concurrent backend writes can cause duplicates and skips.

## Response: `200`

```json
{
  "schema_version": "1.0",
  "tasks": [
    {
      "task_id": "pbt-2f6a1c",
      "question": "Will BTC close above $120,000 on 2026-08-01 (UTC)?",
      "category": "crypto",
      "deadline": "2026-08-01T00:00:00Z",
      "resolution_hint": "Resolves via Binance BTC/USDT daily close.",
      "schema_version": "1.0",
      "created_at": "2026-07-10T09:00:00Z",
      "updated_at": "2026-07-10T09:00:00Z"
    }
  ],
  "next_cursor": "opaque-token-abc123",
  "has_more": false
}
```

The response includes an `ETag` header. Validators send `If-None-Match` on
subsequent polls. A backend can return `304 Not Modified` when there are no new
tasks for that cursor.

## Retry Behavior

- `429` responses include `Retry-After` in seconds.
- Validators retry `429` and `5xx` with exponential backoff plus jitter, bounded
  by `BT_FORECAST_MAX_RETRIES`.
- Validators cap client-side concurrency with `BT_FORECAST_MAX_CONCURRENT_REQUESTS`
  so one process cannot hammer the shared backend.

Legacy `PRIVATEBT_*` environment names are still accepted by the standalone
prototype loader, but new deployments should use `BT_FORECAST_*`.
