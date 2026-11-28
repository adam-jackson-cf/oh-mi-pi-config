class Cache:
    def reset_cache(self) -> int:
        return 0


def warm() -> int:
    return Cache().reset_cache()
