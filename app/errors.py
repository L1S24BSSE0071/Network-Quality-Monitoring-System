"""Helpers that turn validation errors into short, readable messages."""

# Parts of an error location that are not field names.
_LOCATION_PREFIXES = {"body", "path", "query"}


def _friendly_message(err: dict) -> str:
    """Plain-language message for common validation error types."""
    kind = err.get("type", "")
    ctx = err.get("ctx") or {}
    if kind == "missing":
        return "This field is required"
    if kind == "string_too_short":
        minimum = ctx.get("min_length", 1)
        return "Must not be empty" if minimum <= 1 else f"Must be at least {minimum} characters"
    if kind == "string_too_long":
        return f"Must be at most {ctx.get('max_length')} characters"
    if kind == "greater_than_equal":
        return f"Must be at least {ctx.get('ge')}"
    if kind == "greater_than":
        return f"Must be greater than {ctx.get('gt')}"
    if kind == "less_than_equal":
        return f"Must be at most {ctx.get('le')}"
    if kind == "literal_error":
        return f"Must be {ctx.get('expected')}"
    if kind in ("int_parsing", "int_from_float"):
        return "Must be a whole number"
    if kind in ("bool_parsing", "bool_type"):
        return "Must be true or false"
    if kind in ("string_type", "string_sub_type"):
        return "Must be text"
    if kind == "extra_forbidden":
        return "Unknown field"
    if kind == "json_invalid":
        return "Request body is not valid JSON"
    message = err.get("msg", "Invalid value")
    return message.removeprefix("Value error, ")


def format_errors(errors: list[dict]) -> list[dict]:
    """Convert Pydantic error dicts into [{"field": ..., "message": ...}]."""
    formatted = []
    for err in errors:
        field = ".".join(str(part) for part in err.get("loc", ()) if part not in _LOCATION_PREFIXES)
        formatted.append({"field": field or "(request)", "message": _friendly_message(err)})
    return formatted
