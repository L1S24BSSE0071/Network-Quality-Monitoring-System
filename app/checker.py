"""Performs checks against targets and stores the results."""
import logging
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CheckResult, Target

logger = logging.getLogger("nqms.checker")

DEFAULT_TIMEOUT_SECONDS = 10.0

# Extra time allowed on top of the timeout before a check is abandoned. The normal timeout
# message from httpx should win in ordinary cases; this only catches a stuck check.
DEADLINE_GRACE_SECONDS = 1.0

# Identifies the monitor to the sites it checks. Some servers reject requests that carry
# a generic library name, which would be reported as a false outage.
USER_AGENT = "NetworkQualityMonitor/0.1 (+https://github.com/L1S24BSSE0071/Network-Quality-Monitoring-System)"

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
            return f"SSL certificate error: {cause.verify_message}"[:500]
        if isinstance(cause, ssl.SSLError):
            return f"SSL error: {cause}"[:500]
        if isinstance(cause, ConnectionRefusedError):
            return "Connection refused"
        cause = cause.__cause__ or cause.__context__
    text = str(exc).lower()
    if "certificate" in text:
        return "SSL certificate error"
    if "name or service not known" in text or "getaddrinfo" in text or "nodename" in text:
        return "DNS lookup failed (host name could not be resolved)"
    return (f"Connection failed: {exc}" if str(exc) else "Connection failed")[:500]


def _run_http_check(
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
            headers={"User-Agent": USER_AGENT},
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
        return CheckOutcome(is_up=False, error_message=f"Invalid URL: {exc}"[:500])
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


def perform_http_check(
    url: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    transport: Optional[httpx.BaseTransport] = None,
) -> CheckOutcome:
    """Run an HTTP check that is guaranteed to return within about timeout_seconds.

    httpx applies its timeout to each stage of a request, and the operating system's DNS
    look-up is not covered by it at all, so a hung resolver could block a check for far
    longer. The check therefore runs in a helper thread and is abandoned, and reported as
    timed out, once the overall deadline passes. An abandoned thread is a daemon thread: it
    ends by itself when the look-up returns, its result is discarded, and it never blocks
    shutdown.

    Never raises; see _run_http_check for the meaning of the outcome.
    """
    if transport is not None:
        return _run_http_check(url, timeout_seconds, transport)

    box: dict = {}

    def worker() -> None:
        box["outcome"] = _run_http_check(url, timeout_seconds, transport)

    thread = threading.Thread(target=worker, name="http-check", daemon=True)
    thread.start()
    thread.join(timeout_seconds + DEADLINE_GRACE_SECONDS)
    if thread.is_alive():
        logger.warning("Check of %s abandoned after %gs (stuck, probably in DNS)", url, timeout_seconds)
        return CheckOutcome(is_up=False, error_message=f"Timed out after {timeout_seconds:g} seconds")
    return box["outcome"]


def run_check_and_store(
    db: Session, target: Target, *, require_enabled: bool = False
) -> Optional[CheckResult]:
    """Run the check configured for the target and save the result.

    Returns None if the target was deleted, disabled (when require_enabled is True),
    or pointed at a different host/URL while the check was running, so a stale
    in-flight check can never overwrite a changed or disabled target.
    """
    if target.check_type not in SUPPORTED_CHECK_TYPES:
        raise NotImplementedError(f"Check type '{target.check_type}' is not supported yet")
    target_id, url, check_type = target.id, target.host_or_url, target.check_type
    # End the read transaction first. Otherwise this session keeps its pooled database
    # connection checked out for the whole (possibly 10 s) network call, and a few dozen
    # slow targets would use up the pool and make every API request wait.
    db.rollback()
    outcome = perform_http_check(url)
    current = db.get(Target, target_id)
    if (
        current is None
        or current.host_or_url != url
        or current.check_type != check_type
        or (require_enabled and not current.enabled)
    ):
        db.rollback()
        return None
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
    return result
