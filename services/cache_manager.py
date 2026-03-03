import json
import time
from pathlib import Path


class CacheManager:
    def __init__(self, cache_dir: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.cache_dir / f"{safe_key}.json"

    def get(self, key: str, ttl_seconds: int) -> dict | None:
        path = self._file_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                entry = json.load(f)
            age = time.time() - entry["timestamp"]
            if age > ttl_seconds:
                return None
            return entry["data"]
        except (json.JSONDecodeError, KeyError):
            return None

    def set(self, key: str, data) -> None:
        path = self._file_path(key)
        entry = {"timestamp": time.time(), "data": data}
        with open(path, "w") as f:
            json.dump(entry, f, indent=2)

    def get_age_seconds(self, key: str) -> int | None:
        path = self._file_path(key)
        if not path.exists():
            return None
        try:
            with open(path, "r") as f:
                entry = json.load(f)
            return int(time.time() - entry["timestamp"])
        except (json.JSONDecodeError, KeyError):
            return None

    def invalidate(self, key: str) -> None:
        path = self._file_path(key)
        if path.exists():
            path.unlink()

    def invalidate_all(self) -> None:
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
