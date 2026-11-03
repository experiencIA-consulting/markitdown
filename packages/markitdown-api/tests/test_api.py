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
os.environ["PUBLIC_BASE_URL"] = "http://testserver"

from fastapi.testclient import TestClient

from markitdown_api import main
from markitdown_api.job_queue import QUEUE_NAME
from markitdown_api.models import Account, Conversion, utcnow
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
        conversion_id = queued.json()["id"]
        assert (
            client.get(
                f"/v1/conversions/{conversion_id}", headers={"X-API-Key": api_key}
            ).json()["status"]
            == "queued"
        )

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
