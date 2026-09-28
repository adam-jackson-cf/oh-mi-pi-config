"""Configuration accessors for the retry worker."""

DEFAULT_SETTINGS = {"maxAttempts": 3, "queueName": "default"}


def get_max_attempts(settings: dict[str, object]) -> int:
    """Read the retry limit, preferring the renamed configuration key."""
    if "maxAttempts" in settings:
        value = settings["maxAttempts"]
    elif "maxRetries" in settings:
        value = settings["maxRetries"]
    else:
        value = DEFAULT_SETTINGS["maxAttempts"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("maxAttempts must be a non-negative integer")
    return value
