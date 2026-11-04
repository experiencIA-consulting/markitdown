# MarkItDown REST API

REST API and background worker for converting files and public HTTP(S) URLs with MarkItDown.

## Local and Dokploy deployment

1. Copy `.env.example` to `.env` in this directory and replace `JWT_SECRET`, `API_KEY_PEPPER`, and `POSTGRES_PASSWORD` with different random URL-safe values (for example, `python -c "import secrets; print(secrets.token_hex(32))"`). Do not commit `.env`.
2. Set `PUBLIC_BASE_URL` to the HTTPS domain assigned to the `api` service in Dokploy.
3. Configure SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, and, when required, credentials). Email verification is required by default; the API refuses startup if it is enabled without SMTP configuration.
4. In Dokploy, deploy `packages/markitdown-api/compose.yaml`, build from the repository root context, route the domain to the `admin-web` service on port `80`, and set secrets as Dokploy environment variables. `admin-web` serves the SPA and proxies `/v1/*`, `/health/*` and the email landing pages to `api:8000`.
5. Verify `/health/live` and `/health/ready` after deployment. The API also applies Alembic migrations automatically at startup.
6. Provision the first administrator once the containers are healthy:

```bash
docker compose -f packages/markitdown-api/compose.yaml exec api \
  python -m markitdown_api.admin_cli bootstrap-admin admin@example.com
```

The command sends an invitation to `/accept-invitation`, where the administrator sets a password. If the email already exists, it promotes that account to `admin` instead.

The SPA is available at `/login`, `/dashboard`, `/convert`, `/plans`, `/users`, `/reports/consumption` and `/profile`. Public registration is disabled by default (`PUBLIC_SIGNUP_ENABLED=false`); users must be invited from the administration panel. Web sessions use secure `HttpOnly` cookies plus `X-CSRF-Token`; API clients continue to use `X-API-Key`.

For local Compose, run from the repository root:

```bash
docker compose --env-file packages/markitdown-api/.env \
  -f packages/markitdown-api/compose.yaml up --build
```

The Compose file exposes only the `admin-web` gateway to Dokploy's internal proxy. PostgreSQL and Redis have no published host ports. Redis intentionally has no disk persistence because queued URL payloads can contain sensitive query parameters; if Redis is lost, the worker marks unrecoverable jobs failed. The shared conversion volume is temporary application storage and should not be included in backups. Back up PostgreSQL because usage records are retained indefinitely.

Run package tests from the repository root after installing the package and test extras:

```bash
python -m pip install -e "packages/markitdown-api[test]"
python -m pytest packages/markitdown-api/tests -q
```

## Resource limits

The initial configuration uses one worker process and one conversion at a time, a queue limit of five waiting jobs, one multipart upload being parsed at a time, a 100 MiB input limit, a 50 MiB Markdown output limit, a 2 GiB cap on unclaimed results stored at once, and a 15-minute conversion timeout. The worker container defaults to 768 MiB and one CPU; its conversion child is monitored and terminated above 512 MiB RSS. ZIP inputs are limited to 5,000 entries and 128 MiB of declared expanded data. Limits are configurable through Compose variables (`WORKER_MEMORY_LIMIT`, `WORKER_CPUS`, and the `MAX_*` variables). Keep the child RSS limit below the container memory limit to leave room for the worker and helper processes. The output limit prevents oversized results from being stored, but MarkItDown still produces its Markdown string in memory before this check. A 2 GB VPS running Dokploy is resource-constrained; large or complex documents may exceed the worker memory budget. Run representative load tests before increasing limits or advertising throughput. Keep one API replica initially. Consider an external PostgreSQL service and a larger/separate worker VPS for production load.

Before conversion, the worker classifies file content with Magika, checks the declared extension against the detected type, validates PDF headers and inspects ZIP-based office/EPUB containers within the entry/expanded-size limits. This is input-format validation, not malware scanning or a guarantee that a document is safe to parse. Supported extensions remain tied to the MarkItDown formats installed in the image.

PDFs without OCR use PyMuPDF4LLM first, which preserves table structure better than plain text extraction. If that engine fails or returns empty content, the worker falls back to the built-in MarkItDown PDF converter. OCR-enabled conversions continue through the MarkItDown OCR plugin. Review PyMuPDF/PyMuPDF4LLM licensing (AGPL/commercial) before a commercial launch.

## Account and API-key flow

Public registration is disabled by default. Administrators invite users from `/users`; the invitation expires in seven days and lands on `/accept-invitation`, where the user sets a password. `POST /v1/accounts` remains available for API-only registration only when `PUBLIC_SIGNUP_ENABLED=true`.

1. `POST /v1/web-auth/login` with `{"email":"...","password":"..."}` establishes the web session (same-origin cookies) and returns the CSRF token used by the SPA.
2. Use `Authorization: Bearer <access_token>` from `POST /v1/auth/token` for API token flows, or `X-API-Key` from `POST /v1/api-keys` for conversion endpoints.
3. `GET /v1/account/me`, `PATCH /v1/account/me` and `POST /v1/account/password` manage the signed-in profile. A password change revokes API keys and invalidates the web session.

Password reset starts with `POST /v1/accounts/password-reset`; the email link opens a confirmation page, or an API client can submit the token and new password to `POST /v1/accounts/password-reset/confirm`. Resetting a password revokes existing API keys. No payment processing is included.

## Plans, quotas and administration

The catalog is seeded with Free, Pro weekly/monthly/annual, Lifetime and Business as informational tiers. Seeded plans are inactive until an administrator completes the quota matrix; activation requires conversions/month, input bytes/month, pages/month, requests/minute and file-size values. Paid-tier values must come from the benchmark, not estimates.

- Monthly quotas reset at the start of the natural month in `QUOTA_TIMEZONE` (`America/Mazatlan` by default). Reports accept the browser IANA timezone.
- Successful conversions consume one conversion and `quota_page_units` (real `page_count`, or 1 when page count is not reliable, including images). Failed conversions consume no conversion/page quota but always record processed input bytes and authenticated requests/minute.
- OCR consumes units only when an image/page is actually sent to the provider. OCR must be enabled by the active plan and requested per conversion.
- Business is an individual account in this version; organizations/members are not implemented.
- Admin endpoints: `/v1/admin/overview`, `/v1/admin/users`, `/v1/admin/users/{id}`, `/v1/admin/plans`, `/v1/admin/plans/{id}`, `/v1/admin/reports/consumption` and `/v1/admin/reports/consumption.csv`. Only accounts with `role=admin` can use them.
- `GET /v1/plans` exposes only active plans for the informational catalog.

If no plan is active, non-admin conversions fail with `plan_not_configured`. This is intentional until quotas are approved; administrators may still test with global limits.

## Conversion API

Conversion endpoints require an API key in the `X-API-Key` header. The upload endpoint accepts `multipart/form-data` with one required `file` part and an optional `ocr` boolean (defaults to `false`):

```bash
curl -X POST https://api.example.com/v1/conversions \
  -H 'X-API-Key: YOUR_API_KEY' \
  -F 'file=@report.pdf'
```

An accepted upload responds with HTTP `202 Accepted` and JSON:

```json
{
  "id": "7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
  "status": "queued",
  "status_url": "/v1/conversions/7f3a2c9e54b14d5e8a2b1c0d9e6f1234"
}
```

Poll the relative `status_url` using the same `X-API-Key`. The JSON response has these fields (timestamps are UTC ISO-8601 strings; fields may be `null`):

```json
{
  "id": "7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
  "status": "succeeded",
  "error_code": null,
  "created_at": "2026-11-04T12:00:00Z",
  "started_at": "2026-11-04T12:00:01Z",
  "completed_at": "2026-11-04T12:00:03Z",
  "result_expires_at": "2026-11-05T12:00:03Z",
  "result_url": "/v1/conversions/7f3a2c9e54b14d5e8a2b1c0d9e6f1234/result"
}
```

`status` is one of `queued`, `running`, `succeeded`, `failed`, `expired` or `cancelled`. `result_url` is `null` until the conversion succeeds (and after a result is claimed/downloaded). `error_code` is populated on failure. When ready, `GET result_url` returns the Markdown **as the raw response body**, with `Content-Type: text/markdown; charset=utf-8` and `Content-Disposition: attachment; filename="converted.md"`—it is not a JSON object or a JSON-encoded string. For example, the response body may be:

```markdown
# Quarterly report

| Metric | Value |
| --- | ---: |
| Revenue | 1250 |
```

Submit a public URL (this endpoint returns the same `202` response shape):

```bash
curl -X POST https://api.example.com/v1/conversions/from-url \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/report.pdf"}'
```

The interactive OpenAPI docs at `/docs` include the upload request shape, API-key security requirement, accepted/status JSON examples and the Markdown result media type. A completed result is one-time: after the server finishes sending it, it is deleted. If the transfer is interrupted, it remains available for retry until the 24-hour expiration; an abandoned download claim is released after one hour. A queued input is deleted after processing; failed and expired jobs retain only usage metadata.

The URL fetcher only accepts public HTTP/HTTPS destinations on their standard ports, validates DNS results and redirects, blocks non-public IPs, rejects compressed HTTP transfer encodings, and limits response bytes and duration. This does not guarantee that arbitrary third-party content is safe to parse; workers run in a resource-limited container.

## OCR / Azure OpenAI

The image includes the optional MarkItDown OCR plugin and OpenAI-compatible client. OCR remains off unless the conversion explicitly includes `ocr=true` (multipart form field) or `{"ocr":true}` for URL conversion. It is only available when `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_MODEL` are set. Enabling it sends document images to the configured Azure OpenAI deployment. Do not configure these secrets until external processing has been approved.

## Usage data

`GET /v1/usage` (account bearer token) returns paginated per-conversion records: input bytes and decimal MB, page count when available, output character/UTF-8 byte counts, duration, outcome, and optional OCR provider. Input documents, Markdown, filenames, and complete URLs are not included. Records are retained indefinitely; monitor PostgreSQL growth and backups.

## Limitations to address before broad public launch

- Configure email deliverability and add suitable abuse controls for account registration and password reset at the edge.
- Select and test the exact format extras and Azure deployment/model; “all formats” depends on upstream optional dependencies and external credentials.
- Add an operational PostgreSQL backup/restore procedure and test a restore before relying on indefinite records.
- Decide account deletion/anonymization behavior for indefinitely retained usage records.
- Establish measured throughput and resource sizing for the intended workload, then finish paid-tier quotas.
