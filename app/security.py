import secrets
import time
from typing import Dict, List, Optional
from collections import defaultdict

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

# In-memory rate limiting for prototype admin login attempts
# Tracks failed attempts: {ip_address: [timestamp, ...]}
_failed_login_attempts: Dict[str, List[float]] = defaultdict(list)
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_FAILED_ATTEMPTS = 5

security_scheme = HTTPBearer(auto_error=False)


def get_serializer() -> URLSafeTimedSerializer:
    """Return itsdangerous serializer configured with session secret."""
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="waste-admin-auth")


def check_rate_limit(client_ip: str) -> None:
    """Enforce login rate limiting for academic prototype on LAN."""
    now = time.time()
    attempts = _failed_login_attempts[client_ip]
    # Prune attempts older than the window
    _failed_login_attempts[client_ip] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW_SECONDS]

    if len(_failed_login_attempts[client_ip]) >= MAX_FAILED_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts. Please wait 60 seconds before trying again."
        )


def record_failed_attempt(client_ip: str) -> None:
    """Record a failed login attempt for the given IP address."""
    _failed_login_attempts[client_ip].append(time.time())


def reset_rate_limit(client_ip: str) -> None:
    """Reset failed attempts on successful login."""
    _failed_login_attempts.pop(client_ip, None)


def verify_admin_password(provided_password: str) -> bool:
    """Validate password using constant-time comparison."""
    if not settings.ADMIN_PASSWORD:
        return False
    return secrets.compare_digest(provided_password, settings.ADMIN_PASSWORD)


def create_admin_token() -> str:
    """Generate a cryptographically signed session token using itsdangerous."""
    serializer = get_serializer()
    payload = {
        "sub": "admin",
        "role": "admin",
        "issued_at": time.time(),
    }
    return serializer.dumps(payload)


def verify_admin_token(token: str) -> Dict[str, str]:
    """Verify session token signature, role, and expiration."""
    serializer = get_serializer()
    try:
        payload = serializer.loads(token, max_age=settings.SESSION_TTL_SECONDS)
    except SignatureExpired:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token has expired. Please log in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (BadSignature, Exception):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or malformed authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not isinstance(payload, dict) or payload.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Admin privileges required.",
        )

    return payload


async def require_admin(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_scheme)
) -> Dict[str, str]:
    """Reusable FastAPI dependency to enforce valid admin session token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_admin_token(credentials.credentials)
