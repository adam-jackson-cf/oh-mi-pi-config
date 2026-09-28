"""Event kinds are `stock.<past-tense verb>`; fields are flat and JSON-serialisable."""


def emit(events: list[dict[str, object]], kind: str, **fields: object) -> None:
    events.append({"kind": kind, **fields})
