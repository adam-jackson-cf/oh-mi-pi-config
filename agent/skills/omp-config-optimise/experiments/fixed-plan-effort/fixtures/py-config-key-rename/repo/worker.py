from settings import DEFAULT_SETTINGS, get_max_retries


def worker_settings(overrides: dict[str, object] | None = None) -> dict[str, object]:
    settings = {**DEFAULT_SETTINGS, **(overrides or {})}
    return {"queue": settings["queueName"], "maxRetries": get_max_retries(settings)}
