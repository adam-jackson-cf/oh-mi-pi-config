"""Configuration accessors for the retry worker."""

DEFAULT_SETTINGS = {"maxRetries": 3, "queueName": "default"}


def get_max_retries(settings: dict[str, object]) -> int:
    """Read the retry limit from a loaded settings mapping."""
    value = settings.get("maxRetries", DEFAULT_SETTINGS["maxRetries"])
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("maxRetries must be a non-negative integer")
    return value
