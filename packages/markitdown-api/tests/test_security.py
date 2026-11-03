from markitdown_api.security import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


def test_password_hash_verifies_only_correct_password():
    encoded = hash_password("a-strong-example-password")
    assert verify_password("a-strong-example-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_api_key_is_hashed_and_unique():
    first, _prefix, _key_id = generate_api_key()
    second, _prefix, _key_id = generate_api_key()
    assert first != second
    assert hash_api_key(first, "a" * 32) != first
    assert hash_api_key(first, "a" * 32) != hash_api_key(first, "b" * 32)


def test_session_token_expires_and_rejects_tampering():
    token = create_access_token("account-id", "s" * 32)
    assert decode_access_token(token, "s" * 32) == ("account-id", 0)
    assert decode_access_token(token, "x" * 32) is None
    assert decode_access_token(token + "x", "s" * 32) is None
