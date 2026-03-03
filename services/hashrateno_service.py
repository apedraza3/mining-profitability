import logging

import requests
from rapidfuzz import fuzz

from services.cache_manager import CacheManager
import config

logger = logging.getLogger(__name__)


class HashrateNoService:
    def __init__(self, api_key: str, cache: CacheManager):
        self.api_key = api_key
        self.cache = cache
        self.base_url = config.HASHRATENO_BASE_URL
        self.ttl = config.HASHRATENO_CACHE_TTL

    def _get(self, endpoint: str, params: dict = None) -> list | dict | None:
        if not self.api_key:
            logger.warning("Hashrate.no API key not configured")
            return None
        url = f"{self.base_url}/{endpoint}"
        all_params = {"apiKey": self.api_key}
        if params:
            all_params.update(params)
        try:
            resp = requests.get(url, params=all_params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("Hashrate.no request failed: %s", e)
            return None

    def get_gpu_estimates(self) -> list | None:
        """Fetch all GPU profitability estimates with powerCost=0 (pure revenue)."""
        cached = self.cache.get("gpu_estimates", self.ttl)
        if cached is not None:
            return cached
        data = self._get("gpuEstimates", {"powerCost": 0.00})
        if data is not None:
            self.cache.set("gpu_estimates", data)
        return data

    def get_asic_estimates(self) -> list | None:
        """Fetch all ASIC profitability estimates with powerCost=0 (pure revenue)."""
        cached = self.cache.get("asic_estimates", self.ttl)
        if cached is not None:
            return cached
        data = self._get("asicEstimates", {"powerCost": 0.00})
        if data is not None:
            self.cache.set("asic_estimates", data)
        return data

    def find_model_estimate(self, model_key: str, miner_type: str) -> dict | None:
        """Find the best matching model from cached estimates using fuzzy matching."""
        if miner_type == "GPU":
            estimates = self.get_gpu_estimates()
        else:
            estimates = self.get_asic_estimates()

        if not estimates:
            return None

        best_match = None
        best_score = 0

        for entry in estimates:
            # Try common field names for the device name
            device_name = (
                entry.get("name")
                or entry.get("device")
                or entry.get("model")
                or entry.get("deviceName")
                or ""
            )
            score = fuzz.token_sort_ratio(model_key.lower(), device_name.lower())
            if score > best_score:
                best_score = score
                best_match = entry

        if best_match and best_score >= 70:
            return {
                "raw_data": best_match,
                "matched_name": (
                    best_match.get("name")
                    or best_match.get("device")
                    or best_match.get("model")
                    or best_match.get("deviceName")
                    or "Unknown"
                ),
                "match_confidence": best_score,
            }
        return None

    def get_all_model_names(self) -> list[str]:
        """Get all model names for autocomplete."""
        names = []
        for estimates in [self.get_gpu_estimates(), self.get_asic_estimates()]:
            if not estimates:
                continue
            for entry in estimates:
                name = (
                    entry.get("name")
                    or entry.get("device")
                    or entry.get("model")
                    or entry.get("deviceName")
                )
                if name:
                    names.append(name)
        return sorted(set(names))

    def get_cache_age(self) -> int | None:
        """Return age of the cached data in seconds."""
        gpu_age = self.cache.get_age_seconds("gpu_estimates")
        asic_age = self.cache.get_age_seconds("asic_estimates")
        if gpu_age is not None and asic_age is not None:
            return max(gpu_age, asic_age)
        return gpu_age or asic_age

    def is_configured(self) -> bool:
        return bool(self.api_key)
