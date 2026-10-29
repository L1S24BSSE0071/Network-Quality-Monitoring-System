"""Pydantic schemas: request validation and response shapes."""
import ipaddress
import re
from datetime import datetime
from typing import Literal, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MIN_INTERVAL_SECONDS = 10
MAX_INTERVAL_SECONDS = 3600
MAX_THRESHOLD_MS = 3_600_000

CheckType = Literal["http", "ping"]

# One DNS label: letters/digits/hyphens, not starting or ending with a hyphen.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$"
)

_NUMERIC_HOST_RE = re.compile(r"^[0-9.]+$")


def _is_valid_host(host: str) -> bool:
    """True if host is a valid hostname or IP address (no scheme, port or path).

    Accepts international names (converted to their ASCII form) and a trailing dot.
    Per RFC 1123 / RFC 3696, the top-level label of a domain name cannot be all digits,
    which also rejects malformed numeric addresses such as 256.1.1.1 or example.123.
    """
    if not host or "%" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    host = host.removesuffix(".")  # fully qualified name with a trailing dot
    if not host:
        return False
    try:
        host = host.encode("idna").decode("ascii")
        if "xn--" in host.lower():
            host.lower().encode("ascii").decode("idna")
    except UnicodeError:
        return False
    if _NUMERIC_HOST_RE.match(host) or host.rsplit(".", 1)[-1].isdigit():
        return False
    return bool(_HOSTNAME_RE.match(host))


def validate_host_or_url(value: str, check_type: str) -> str:
    """Validate the host/URL for the given check type; return it unchanged if valid."""
    if any(ord(ch) <= 32 or ord(ch) == 127 or ch == "\\" for ch in value):
        raise ValueError("Host or URL must not contain spaces, backslashes or control characters")
    if check_type == "http":
        try:
            parsed = urlparse(value)
        except ValueError:
            raise ValueError("URL does not contain a valid host name")
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL must start with http:// or https://")
        try:
            hostname = parsed.hostname
            port = parsed.port  # raises ValueError for a malformed or out-of-range port
        except ValueError:
            raise ValueError("URL has an invalid port")
        if port == 0 or parsed.netloc.endswith(":"):
            raise ValueError("URL has an invalid port")
        if not hostname or not _is_valid_host(hostname):
            raise ValueError("URL does not contain a valid host name")
        if "@" in parsed.netloc:
            raise ValueError("URL must not contain a user name or password")
    else:  # ping
        if not _is_valid_host(value):
            raise ValueError("Ping target must be a host name or IP address (no http://, port or path)")
    return value


def validate_thresholds(warning_ms: int, critical_ms: int) -> None:
    """The warning threshold must be lower than the critical threshold."""
    if warning_ms >= critical_ms:
        raise ValueError("warning_ms must be lower than critical_ms")


def _check_no_control_chars(value: Optional[str]) -> Optional[str]:
    if value is not None and any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError("Name must not contain control characters")
    return value


class TargetBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    host_or_url: str = Field(min_length=1, max_length=2048)
    check_type: CheckType = "http"
    interval_seconds: int = Field(
        default=60,
        ge=MIN_INTERVAL_SECONDS,
        le=MAX_INTERVAL_SECONDS,
        description=f"Seconds between checks ({MIN_INTERVAL_SECONDS}-{MAX_INTERVAL_SECONDS}).",
    )
    warning_ms: int = Field(
        default=500,
        gt=0,
        le=MAX_THRESHOLD_MS,
        description="Latency (ms) above which a warning is raised.",
    )
    critical_ms: int = Field(
        default=1000,
        gt=0,
        le=MAX_THRESHOLD_MS,
        description="Latency (ms) above which a critical event is raised.",
    )
    enabled: bool = True

    @field_validator("name", "host_or_url", mode="before")
    @classmethod
    def strip_whitespace(cls, value):
        """Trim spaces so '   ' counts as empty and is rejected."""
        return value.strip() if isinstance(value, str) else value

    @field_validator("interval_seconds", "warning_ms", "critical_ms", mode="before")
    @classmethod
    def reject_bool_for_int(cls, value):
        if isinstance(value, bool):
            raise ValueError("Must be a whole number")
        return value

    @field_validator("enabled", mode="before")
    @classmethod
    def require_strict_bool(cls, value):
        if not isinstance(value, bool):
            raise ValueError("Must be true or false")
        return value

    @field_validator("name")
    @classmethod
    def validate_name_chars(cls, value: str) -> str:
        return _check_no_control_chars(value)


class TargetCreate(TargetBase):
    """Body for creating a target; all cross-field rules are checked here."""

    model_config = ConfigDict(extra="forbid")  # a misspelled field is an error, not ignored

    @model_validator(mode="after")
    def check_rules(self):
        validate_host_or_url(self.host_or_url, self.check_type)
        validate_thresholds(self.warning_ms, self.critical_ms)
        return self


class TargetUpdate(BaseModel):
    """Body for editing a target; every field is optional.

    Rules that depend on the stored values (e.g. only warning_ms is sent) are
    re-checked against the merged target in the API layer.
    """

    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    host_or_url: Optional[str] = Field(default=None, min_length=1, max_length=2048)
    check_type: Optional[CheckType] = None
    interval_seconds: Optional[int] = Field(
        default=None, ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS
    )
    warning_ms: Optional[int] = Field(default=None, gt=0, le=MAX_THRESHOLD_MS)
    critical_ms: Optional[int] = Field(default=None, gt=0, le=MAX_THRESHOLD_MS)
    enabled: Optional[bool] = None

    @field_validator("name", "host_or_url", mode="before")
    @classmethod
    def strip_whitespace(cls, value):
        if value is None:
            raise ValueError("Must be text")
        return value.strip() if isinstance(value, str) else value

    @field_validator("interval_seconds", "warning_ms", "critical_ms", mode="before")
    @classmethod
    def reject_bool_or_null_for_int(cls, value):
        if value is None or isinstance(value, bool):
            raise ValueError("Must be a whole number")
        return value

    @field_validator("enabled", mode="before")
    @classmethod
    def reject_null_for_bool(cls, value):
        if not isinstance(value, bool):
            raise ValueError("Must be true or false")
        return value

    @field_validator("check_type", mode="before")
    @classmethod
    def reject_null_for_check_type(cls, value):
        if value is None:
            raise ValueError("Must be 'http' or 'ping'")
        return value

    @field_validator("name")
    @classmethod
    def validate_name_chars(cls, value: Optional[str]) -> Optional[str]:
        return _check_no_control_chars(value)


class TargetRead(BaseModel):
    """Target as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    host_or_url: str
    check_type: CheckType
    interval_seconds: int
    warning_ms: int
    critical_ms: int
    enabled: bool
    created_at: datetime


class CheckResultRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_id: int
    checked_at: datetime
    is_up: bool
    latency_ms: Optional[float]
    http_status: Optional[int]
    error_message: Optional[str]


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    target_id: int
    occurred_at: datetime
    severity: Literal["warning", "critical", "recovered"]
    message: str
    latency_ms: Optional[float]
