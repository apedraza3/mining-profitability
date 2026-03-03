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
        """Find the best matching model from cached estimates using fuzzy matching.
        Hashrate.no returns a dict keyed by slug, each value has:
          device: {name, brand}
          profit: {ticker, yield, revenue, profit}
          revenue: {ticker, yield, revenue, profit}
        """
        if miner_type == "GPU":
            estimates = self.get_gpu_estimates()
        else:
            estimates = self.get_asic_estimates()

        if not estimates or not isinstance(estimates, dict):
            return None

        best_match = None
        best_slug = None
        best_score = 0

        query = model_key.lower()
        for slug, entry in estimates.items():
            device = entry.get("device", {})
            device_name = device.get("name", "")
            name = device_name.lower()
            # Three-way scoring:
            #   token_set_ratio  — handles brand prefix differences
            #                      ("ElphaPex DG1" matches "DG1")
            #   partial_ratio   — handles substring matches
            #                      ("S21+ Hyd" finds "Antminer S21+ Hydro")
            #   ratio           — penalises length/content mismatches
            #                      (prevents "Fluminer L1" matching "L1")
            token_set = fuzz.token_set_ratio(query, name)
            partial = fuzz.partial_ratio(query, name)
            ratio = fuzz.ratio(query, name)
            score = token_set * 0.4 + partial * 0.3 + ratio * 0.3
            if score > best_score:
                best_score = score
                best_match = entry
                best_slug = slug

        if best_match and best_score >= 80:
            device = best_match.get("device", {})
            # revenue.revenue is the daily revenue (with powerCost=0, profit=revenue)
            rev_data = best_match.get("revenue", {})
            return {
                "raw_data": best_match,
                "matched_slug": best_slug,
                "matched_name": device.get("name", best_slug),
                "match_confidence": best_score,
                "daily_revenue": float(rev_data.get("revenue", 0) or 0),
                "best_coin": rev_data.get("ticker", ""),
            }
        return None

    def get_all_model_names(self) -> list[str]:
        """Get all model names for autocomplete."""
        names = []
        for estimates in [self.get_gpu_estimates(), self.get_asic_estimates()]:
            if not estimates or not isinstance(estimates, dict):
                continue
            for slug, entry in estimates.items():
                device = entry.get("device", {})
                name = device.get("name")
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
