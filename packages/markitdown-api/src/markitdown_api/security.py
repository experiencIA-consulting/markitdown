import base64
import hashlib
import hmac
import json
import secrets
import time
from uuid import uuid4

PBKDF2_ROUNDS = 600_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, rounds, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), _unb64(salt), int(rounds)
        )
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


def generate_api_key() -> tuple[str, str, str]:
    key = f"mk_live_{secrets.token_urlsafe(32)}"
    return key, key[:16], uuid4().hex


def hash_api_key(key: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), key.encode(), hashlib.sha256).hexdigest()


def generate_account_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()


def create_access_token(
    account_id: str, secret: str, expires_seconds: int = 3600, version: int = 0
) -> str:
    now = int(time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": account_id,
        "iat": now,
        "exp": now + expires_seconds,
        "iss": "markitdown-api",
        "ver": version,
    }
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signed = f"{encoded_header}.{encoded_payload}"
    signature = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).digest()
    return f"{signed}.{_b64(signature)}"


def decode_access_token(token: str, secret: str) -> tuple[str, int] | None:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".", 2)
        signed = f"{encoded_header}.{encoded_payload}"
        expected = hmac.new(secret.encode(), signed.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(encoded_signature)):
            return None
        header = json.loads(_unb64(encoded_header))
        payload = json.loads(_unb64(encoded_payload))
        if header != {"alg": "HS256", "typ": "JWT"}:
            return None
        if payload.get("iss") != "markitdown-api" or int(payload.get("exp", 0)) <= int(
            time.time()
        ):
            return None
        subject = payload.get("sub")
        version = payload.get("ver")
        if not isinstance(subject, str) or type(version) is not int:
            return None
        return subject, version
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
