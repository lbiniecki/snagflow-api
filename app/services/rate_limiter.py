"""
Simple in-memory rate limiter.
Limits requests per IP per time window.
"""
import time
from collections import defaultdict
from fastapi import HTTPException, Request

# Store: {ip: [(timestamp, ...),]}
_requests: dict[str, list[float]] = defaultdict(list)


def rate_limit(request: Request, max_requests: int = 10, window_seconds: int = 60):
    """
    Check rate limit for a request. Raises 429 if exceeded.
    Default: 10 requests per 60 seconds per IP.
    """
    ip = request.client.host if request.client else "unknown"
    now = time.time()

    # Clean old entries
    _requests[ip] = [t for t in _requests[ip] if now - t < window_seconds]

    if len(_requests[ip]) >= max_requests:
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Try again in {window_seconds} seconds."
        )

    _requests[ip].append(now)
