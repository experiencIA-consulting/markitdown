import asyncio
import json
import os
import tempfile
from datetime import timedelta
from pathlib import Path

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="markitdown-api-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'api.sqlite').as_posix()}"
os.environ["STORAGE_DIR"] = str(_TEST_ROOT / "data")
os.environ["JWT_SECRET"] = "j" * 40
os.environ["API_KEY_PEPPER"] = "p" * 40
os.environ["EMAIL_VERIFICATION_REQUIRED"] = "false"
os.environ["PUBLIC_SIGNUP_ENABLED"] = "true"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

from fastapi.testclient import TestClient

from markitdown_api import main
from markitdown_api.job_queue import QUEUE_NAME
from markitdown_api.models import Account, Conversion, Plan, PlanAssignment, utcnow
from markitdown_api.worker import _process_job


class FakeRedis:
    def __init__(self):
        self.queues = {}
        self.counters = {}
        self.values = {}

    async def ping(self):
        return True

    async def aclose(self):
        return None

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, _key, _seconds):
        return True

    async def exists(self, key):
        return key in self.values

    async def eval(self, _script, _numkeys, queue, maximum, value):
        items = self.queues.setdefault(queue, [])
        if len(items) >= int(maximum):
            return 0
        items.insert(0, value)
        return 1


def test_conversion_openapi_contract():
    schema = main.app.openapi()
    upload = schema["paths"]["/v1/conversions"]["post"]
    components = schema["components"]["schemas"]

    assert {"APIKeyHeader": []} in upload["security"]
    assert schema["components"]["securitySchemes"]["APIKeyHeader"] == {
        "type": "apiKey",
        "in": "header",
        "name": "X-API-Key",
    }

    multipart = upload["requestBody"]["content"]["multipart/form-data"]["schema"]
    assert multipart["required"] == ["file"]
    assert multipart["properties"]["file"] == {"type": "string", "format": "binary"}
    assert multipart["properties"]["ocr"]["type"] == "boolean"

    accepted = upload["responses"]["202"]["content"]["application/json"]["schema"]
    assert accepted["$ref"].endswith("/ConversionAcceptedResponse")
    assert components["ConversionAcceptedResponse"]["examples"][0] == {
        "id": "7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
        "status": "queued",
        "status_url": "/v1/conversions/7f3a2c9e54b14d5e8a2b1c0d9e6f1234",
    }

    status = schema["paths"]["/v1/conversions/{conversion_id}"]["get"]
    status_schema = status["responses"]["200"]["content"]["application/json"]["schema"]
    assert status_schema["$ref"].endswith("/ConversionStatusResponse")
    assert set(components["ConversionStatusResponse"]["properties"]) == {
        "id",
        "status",
        "error_code",
        "created_at",
        "started_at",
        "completed_at",
        "result_expires_at",
        "result_url",
    }

    result = schema["paths"]["/v1/conversions/{conversion_id}/result"]["get"]
    markdown = result["responses"]["200"]["content"]["text/markdown"]["schema"]
    assert markdown["type"] == "string"
    assert markdown["example"].startswith("# Converted document")
    assert "application/json" not in result["responses"]["200"]["content"]


def test_account_key_conversion_ownership_and_usage(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(
        main.Redis, "from_url", classmethod(lambda cls, *args, **kwargs: fake_redis)
    )

    with TestClient(main.app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        fake_redis.values["markitdown:worker:heartbeat"] = "ok"
        assert client.get("/health/ready").status_code == 200

        first = client.post(
            "/v1/accounts",
            json={
                "email": "first@example.test",
                "password": "a-long-test-password-123",
            },
        )
        assert first.status_code == 201
        first_data = first.json()
        api_key = first_data["api_key"]
        access_token = first_data["access_token"]

        with main.SessionLocal() as db:
            plan = Plan(
                id="test-plan",
                code="test_free",
                name="Test plan",
                billing_period="monthly",
                is_active=True,
                max_conversions_month=300,
                max_input_bytes_month=100 * 1024 * 1024,
                max_pages_month=300,
                requests_per_minute=30,
                max_file_bytes=10 * 1024 * 1024,
                max_batch_files=1,
                ocr_enabled=False,
            )
            db.add(plan)
            db.add(
                PlanAssignment(
                    id="test-assignment",
                    account_id=first_data["id"],
                    plan_id=plan.id,
                    starts_at=utcnow() - timedelta(minutes=1),
                )
            )
            db.commit()

        login = client.post(
            "/v1/auth/token",
            json={
                "email": "first@example.test",
                "password": "a-long-test-password-123",
            },
        )
        assert login.status_code == 200

        malicious = client.post(
            "/v1/conversions/from-url",
            headers={"X-API-Key": api_key},
            json={"url": "http://127.0.0.1/private"},
        )
        assert malicious.status_code == 422

        queued = client.post(
            "/v1/conversions/from-url",
            headers={"X-API-Key": api_key},
            json={"url": "https://example.com/report.pdf"},
        )
        assert queued.status_code == 202
        queued_data = queued.json()
        assert set(queued_data) == {"id", "status", "status_url"}
        assert queued_data["status"] == "queued"
        conversion_id = queued_data["id"]
        queued_status = client.get(
            f"/v1/conversions/{conversion_id}", headers={"X-API-Key": api_key}
        )
        assert queued_status.status_code == 200
        assert set(queued_status.json()) == {
            "id",
            "status",
            "error_code",
            "created_at",
            "started_at",
            "completed_at",
            "result_expires_at",
            "result_url",
        }
        assert queued_status.json()["status"] == "queued"
        assert queued_status.json()["result_url"] is None

        with main.SessionLocal() as db:
            conversion = db.get(Conversion, conversion_id)
            conversion.status = "succeeded"
            conversion.result_expires_at = utcnow() + timedelta(minutes=10)
            db.commit()
        result_file = main.result_path(main.get_settings(), conversion_id)
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text("# converted", encoding="utf-8")
        downloaded = client.get(
            f"/v1/conversions/{conversion_id}/result", headers={"X-API-Key": api_key}
        )
        assert downloaded.status_code == 200
        assert downloaded.text == "# converted"
        assert downloaded.headers["content-type"] == "text/markdown; charset=utf-8"
        assert not result_file.exists()
        assert (
            client.get(
                f"/v1/conversions/{conversion_id}/result",
                headers={"X-API-Key": api_key},
            ).status_code
            == 410
        )

        uploaded = client.post(
            "/v1/conversions",
            headers={"X-API-Key": api_key},
            files={"file": ("hello.txt", b"hello upload", "text/plain")},
        )
        assert uploaded.status_code == 202
        assert uploaded.json()["status"] == "queued"
        upload_id = uploaded.json()["id"]
        upload_job = next(
            json.loads(raw)
            for raw in fake_redis.queues[QUEUE_NAME]
            if json.loads(raw).get("conversion_id") == upload_id
        )
        asyncio.run(_process_job(main.get_settings(), upload_job))
        completed = client.get(
            f"/v1/conversions/{upload_id}", headers={"X-API-Key": api_key}
        )
        assert completed.json()["status"] == "succeeded"
        converted = client.get(
            f"/v1/conversions/{upload_id}/result", headers={"X-API-Key": api_key}
        )
        assert converted.status_code == 200
        assert converted.text == "hello upload"

        mismatched = client.post(
            "/v1/conversions",
            headers={"X-API-Key": api_key},
            files={"file": ("not-a-pdf.txt", b"%PDF-1.7\nnot text", "text/plain")},
        )
        assert mismatched.status_code == 202
        mismatched_id = mismatched.json()["id"]
        mismatched_job = next(
            json.loads(raw)
            for raw in fake_redis.queues[QUEUE_NAME]
            if json.loads(raw).get("conversion_id") == mismatched_id
        )
        asyncio.run(_process_job(main.get_settings(), mismatched_job))
        rejected = client.get(
            f"/v1/conversions/{mismatched_id}", headers={"X-API-Key": api_key}
        )
        assert rejected.json()["status"] == "failed"
        assert rejected.json()["error_code"] == "file_type_mismatch"
        assert not main.input_path(main.get_settings(), mismatched_id).exists()
        assert not main.result_path(main.get_settings(), mismatched_id).exists()

        second = client.post(
            "/v1/accounts",
            json={
                "email": "second@example.test",
                "password": "another-long-password-123",
            },
        )
        second_key = second.json()["api_key"]
        foreign = client.get(
            f"/v1/conversions/{conversion_id}", headers={"X-API-Key": second_key}
        )
        assert foreign.status_code == 404

        keys = client.get(
            "/v1/api-keys", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert keys.status_code == 200
        usage = client.get(
            "/v1/usage", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert usage.status_code == 200
        assert {row["source_type"] for row in usage.json()["items"]} == {
            "upload",
            "url",
        }
        with main.SessionLocal() as db:
            account = db.get(Account, first_data["id"])
            account.session_version += 1
            db.commit()
        assert (
            client.get(
                "/v1/api-keys", headers={"Authorization": f"Bearer {access_token}"}
            ).status_code
            == 401
        )
