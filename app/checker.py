"""Performs checks against targets and stores the results."""
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CheckResult, Target

DEFAULT_TIMEOUT_SECONDS = 10.0

# Check types that can actually be run. Ping is added later if time allows.
SUPPORTED_CHECK_TYPES = {"http"}

# Loading the CA certificates takes several hundred milliseconds, so it is done once
# and shared by every check instead of once per check. SSL contexts are thread-safe.
_SSL_CONTEXT = httpx.create_ssl_context()


@dataclass
class CheckOutcome:
    """Result of a single check, before it is stored."""

    is_up: bool
    latency_ms: Optional[float] = None
    http_status: Optional[int] = None
    error_message: Optional[str] = None


def _describe_connect_error(exc: httpx.ConnectError) -> str:
    """Turn a low-level connection error into a short, readable message."""
    cause = exc.__cause__
    # Walk the exception chain to find the root cause (DNS, TLS, refused...).
    while cause is not None:
        if isinstance(cause, socket.gaierror):
            return "DNS lookup failed (host name could not be resolved)"
        if isinstance(cause, ssl.SSLCertVerificationError):
            return f"SSL certificate error: {cause.verify_message}"
        if isinstance(cause, ssl.SSLError):
            return f"SSL error: {cause}"
        if isinstance(cause, ConnectionRefusedError):
            return "Connection refused"
        cause = cause.__cause__ or cause.__context__
    text = str(exc).lower()
    if "certificate" in text:
        return "SSL certificate error"
    if "name or service not known" in text or "getaddrinfo" in text or "nodename" in text:
        return "DNS lookup failed (host name could not be resolved)"
    return f"Connection failed: {exc}" if str(exc) else "Connection failed"


def perform_http_check(
    url: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Optional[httpx.BaseTransport] = None,
) -> CheckOutcome:
    """Send one GET request and measure the response time.

    The response time is the time until the response headers arrive (including
    connection set-up, TLS and any redirects). The body is not downloaded, so a
    large page costs no bandwidth and does not distort the measurement.

    Never raises: every failure is returned as a CheckOutcome with is_up=False
    and an error_message. A response with status 400 or above counts as down,
    but its latency and status code are still recorded.

    `transport` is only used by tests to avoid real network access.
    """
    try:
        with httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
            verify=_SSL_CONTEXT,
        ) as client:
            # Start the timer after the client is built so set-up work is not counted.
            start = time.perf_counter()
            with client.stream("GET", url) as response:
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                status_code = response.status_code
                reason = response.reason_phrase
    except httpx.TimeoutException:
        return CheckOutcome(is_up=False, error_message=f"Timed out after {timeout_seconds:g} seconds")
    except httpx.ConnectError as exc:
        return CheckOutcome(is_up=False, error_message=_describe_connect_error(exc))
    except httpx.TooManyRedirects:
        return CheckOutcome(is_up=False, error_message="Too many redirects")
    except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
        return CheckOutcome(is_up=False, error_message=f"Invalid URL: {exc}")
    except httpx.HTTPError as exc:
        return CheckOutcome(is_up=False, error_message=f"Request failed: {type(exc).__name__}: {exc}"[:500])
    except Exception as exc:  # last resort so a check can never crash the caller
        return CheckOutcome(is_up=False, error_message=f"Unexpected error: {type(exc).__name__}: {exc}"[:500])

    if status_code >= 400:
        return CheckOutcome(
            is_up=False,
            latency_ms=latency_ms,
            http_status=status_code,
            error_message=f"HTTP {status_code} {reason}".strip(),
        )
    return CheckOutcome(is_up=True, latency_ms=latency_ms, http_status=status_code)


def run_check_and_store(db: Session, target: Target) -> Optional[CheckResult]:
    """Run the check configured for the target and save the result.

    Returns None if the target was deleted while the check was running, in which
    case nothing is stored.
    """
    if target.check_type not in SUPPORTED_CHECK_TYPES:
        raise NotImplementedError(f"Check type '{target.check_type}' is not supported yet")
    target_id = target.id
    outcome = perform_http_check(target.host_or_url)
    result = CheckResult(
        target_id=target_id,
        is_up=outcome.is_up,
        latency_ms=outcome.latency_ms,
        http_status=outcome.http_status,
        error_message=outcome.error_message,
    )
    db.add(result)
    try:
        db.commit()
    except IntegrityError:
        # The foreign key failed: the target no longer exists.
        db.rollback()
        return None
    db.refresh(result)
    return result
