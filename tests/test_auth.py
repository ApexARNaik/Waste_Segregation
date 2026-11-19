import time
import pytest
from fastapi import HTTPException
from app.config import settings
from app.security import (
    create_admin_token,
    verify_admin_password,
    verify_admin_token,
    get_serializer,
)


def test_correct_password_validation():
    """Verify correct admin password produces True."""
    assert verify_admin_password(settings.ADMIN_PASSWORD) is True


def test_incorrect_password_rejected():
    """Verify incorrect password produces False."""
    assert verify_admin_password("wrong_password") is False
    assert verify_admin_password("") is False


def test_token_creation_and_verification():
    """Verify generated admin token can be verified and payload is correct."""
    token = create_admin_token()
    payload = verify_admin_token(token)
    assert payload["sub"] == "admin"
    assert payload["role"] == "admin"


def test_invalid_token_rejected():
    """Verify forged, malformed, or tampered tokens are rejected with 401."""
    with pytest.raises(HTTPException) as exc_info:
        verify_admin_token("invalid.token.value")
    assert exc_info.value.status_code == 401
    assert "Invalid or malformed" in exc_info.value.detail


def test_expired_token_rejected():
    """Verify expired token is rejected with 401."""
    token = create_admin_token()

    original_ttl = settings.SESSION_TTL_SECONDS
    try:
        # Setting SESSION_TTL_SECONDS to -1 forces immediate expiration
        settings.SESSION_TTL_SECONDS = -1
        with pytest.raises(HTTPException) as exc_info:
            verify_admin_token(token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()
    finally:
        settings.SESSION_TTL_SECONDS = original_ttl


def test_insufficient_privileges_rejected():
    """Verify token without admin role is rejected with 403."""
    serializer = get_serializer()
    token = serializer.dumps({"sub": "guest", "role": "viewer"})

    with pytest.raises(HTTPException) as exc_info:
        verify_admin_token(token)
    assert exc_info.value.status_code == 403
    assert "Admin privileges required" in exc_info.value.detail
