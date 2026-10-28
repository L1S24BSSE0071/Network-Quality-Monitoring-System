"""Helpers that turn validation errors into short, readable messages."""


def format_errors(errors: list[dict]) -> list[dict]:
    """Convert Pydantic error dicts into [{"field": ..., "message": ...}]."""
    formatted = []
    for err in errors:
        field = ".".join(str(part) for part in err.get("loc", ()) if part != "body")
        message = err.get("msg", "Invalid value")
        message = message.removeprefix("Value error, ")
        formatted.append({"field": field or "(request)", "message": message})
    return formatted
