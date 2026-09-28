from settings import DEFAULT_SETTINGS, get_max_attempts


def worker_settings(overrides: dict[str, object] | None = None) -> dict[str, object]:
    supplied = overrides or {}
    settings = {**DEFAULT_SETTINGS, **supplied}
    return {"queue": settings["queueName"], "maxAttempts": get_max_attempts(supplied)}
