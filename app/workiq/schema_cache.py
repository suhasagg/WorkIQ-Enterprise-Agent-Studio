import time


class SchemaCache:
    def __init__(self, ttl: float = 900):
        self.ttl = ttl
        self.data: dict[str, tuple[float, dict]] = {}

    def get(self, k: str):
        x = self.data.get(k)
        if not x or x[0] < time.time():
            return None
        return x[1]

    def put(self, k: str, v: dict) -> None:
        self.data[k] = (time.time() + self.ttl, v)


schema_cache = SchemaCache()
