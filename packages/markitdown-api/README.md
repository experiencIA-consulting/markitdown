# MarkItDown REST API

REST API and background worker for converting files and public HTTP(S) URLs with MarkItDown.

## Local and Dokploy deployment

1. Copy `.env.example` to `.env` in this directory and replace `JWT_SECRET`, `API_KEY_PEPPER`, and `POSTGRES_PASSWORD` with different random URL-safe values (for example, `python -c "import secrets; print(secrets.token_hex(32))"`). Do not commit `.env`.
2. Set `PUBLIC_BASE_URL` to the HTTPS domain assigned to the `api` service in Dokploy.
3. Configure SMTP (`SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, and, when required, credentials). Email verification is required by default; the API refuses startup if it is enabled without SMTP configuration.
4. In Dokploy, deploy `packages/markitdown-api/compose.yaml`, build from the repository root context, route the domain to the `api` service on port `8000`, and set secrets as Dokploy environment variables.
5. Verify `/health/live` and `/health/ready` after deployment.

For local Compose, run from the repository root:

```bash
docker compose --env-file packages/markitdown-api/.env \
  -f packages/markitdown-api/compose.yaml up --build
```

The Compose file exposes only the API to Dokploy's internal proxy. PostgreSQL and Redis have no published host ports. Redis intentionally has no disk persistence because queued URL payloads can contain sensitive query parameters; if Redis is lost, the worker marks unrecoverable jobs failed. The shared conversion volume is temporary application storage and should not be included in backups. Back up PostgreSQL because usage records are retained indefinitely.

Run package tests from the repository root after installing the package and test extras:

```bash
python -m pip install -e "packages/markitdown-api[test]"
python -m pytest packages/markitdown-api/tests -q
```

## Resource limits

The initial configuration uses one worker process and one conversion at a time, a queue limit of five waiting jobs, one multipart upload being parsed at a time, a 100 MiB input limit, a 50 MiB Markdown output limit, a 2 GiB cap on unclaimed results stored at once, and a 15-minute conversion timeout. These are configurable in Compose environment variables. A 2 GB VPS running Dokploy is resource-constrained; large or complex documents may exceed the worker memory limit. Run representative load tests before increasing limits or advertising throughput. Keep one API replica initially. Consider an external PostgreSQL service and a larger/separate worker VPS for production load.

## Account and API-key flow

1. `POST /v1/accounts` with `{"email":"...","password":"at-least-12-chars"}`.
2. Open the verification link delivered by SMTP and confirm it, or call `POST /v1/accounts/verify` with `{"token":"..."}`.
3. `POST /v1/auth/token` with email/password to receive a one-hour account access token.
4. Use that token as `Authorization: Bearer <access_token>` for key management. `POST /v1/api-keys` returns a new API key once; list/revoke keys at `GET/DELETE /v1/api-keys`.
5. Use `X-API-Key: <api-key>` for conversion and usage endpoints.

Password reset starts with `POST /v1/accounts/password-reset`; the email link opens a confirmation page, or an API client can submit the token and new password to `POST /v1/accounts/password-reset/confirm`. Resetting a password revokes existing API keys. No payment processing is included.

## Conversion API

Upload a file:

```bash
curl -X POST https://api.example.com/v1/conversions \
  -H 'X-API-Key: YOUR_API_KEY' \
  -F 'file=@report.pdf'
```

Submit a public URL:

```bash
curl -X POST https://api.example.com/v1/conversions/from-url \
  -H 'X-API-Key: YOUR_API_KEY' \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/report.pdf"}'
```

Both return `202` and a conversion ID. Poll `GET /v1/conversions/{id}` and retrieve the Markdown from `GET /v1/conversions/{id}/result`. A completed result is one-time: after the server finishes sending it, it is deleted. If the transfer is interrupted, it remains available for retry until the 24-hour expiration; an abandoned download claim is released after one hour. A queued input is deleted after processing; failed and expired jobs retain only usage metadata.

The URL fetcher only accepts public HTTP/HTTPS destinations on their standard ports, validates DNS results and redirects, blocks non-public IPs, rejects compressed HTTP transfer encodings, and limits response bytes and duration. This does not guarantee that arbitrary third-party content is safe to parse; workers run in a resource-limited container.

## OCR / Azure OpenAI

The image includes the optional MarkItDown OCR plugin and OpenAI-compatible client. OCR remains off unless the conversion explicitly includes `ocr=true` (multipart form field) or `{"ocr":true}` for URL conversion. It is only available when `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_MODEL` are set. Enabling it sends document images to the configured Azure OpenAI deployment. Do not configure these secrets until external processing has been approved.

## Usage data

`GET /v1/usage` (account bearer token) returns paginated per-conversion records: input bytes and decimal MB, page count when available, output character/UTF-8 byte counts, duration, outcome, and optional OCR provider. Input documents, Markdown, filenames, and complete URLs are not included. Records are retained indefinitely; monitor PostgreSQL growth and backups.

## Limitations to address before broad public launch

- Configure email deliverability and add suitable abuse controls for account registration and password reset at the edge.
- Select and test the exact format extras and Azure deployment/model; “all formats” depends on upstream optional dependencies and external credentials.
- Add schema migrations and an operational backup/restore procedure before changing the database schema or relying on indefinite records.
- Decide account deletion/anonymization behavior for indefinitely retained usage records.
- Establish measured throughput and resource sizing for the intended workload.
