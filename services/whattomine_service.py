import json
import time
import logging

import requests

from services.cache_manager import CacheManager
import config

logger = logging.getLogger(__name__)

# Hashrate unit conversions to raw H/s for WhatToMine queries
HASHRATE_MULTIPLIERS = {
    "H/s": 1,
    "KH/s": 1e3,
    "MH/s": 1e6,
    "GH/s": 1e9,
    "TH/s": 1e12,
    "PH/s": 1e15,
    "EH/s": 1e18,
    "Sol/s": 1,
    "KSol/s": 1e3,
}


class WhatToMineService:
    def __init__(self, cache: CacheManager):
        self.cache = cache
        self.base_url = config.WHATTOMINE_BASE_URL
        self.ttl = config.WHATTOMINE_CACHE_TTL
        self._last_request_time = 0

    def _throttled_get(self, url: str, params: dict = None) -> dict | None:
        elapsed = time.time() - self._last_request_time
        if elapsed < config.WHATTOMINE_REQUEST_DELAY:
            time.sleep(config.WHATTOMINE_REQUEST_DELAY - elapsed)
        try:
            resp = requests.get(url, params=params, timeout=15)
            self._last_request_time = time.time()
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.error("WhatToMine request failed: %s", e)
            return None

    def get_coins_index(self) -> dict | None:
        """Fetch the full GPU-mineable coins list."""
        cached = self.cache.get("coins_index", 3600)
        if cached:
            return cached
        data = self._throttled_get(f"{self.base_url}/coins.json")
        if data:
            self.cache.set("coins_index", data)
        return data

    def get_asic_index(self) -> dict | None:
        """Fetch the full ASIC-mineable coins list."""
        cached = self.cache.get("asic_index", 3600)
        if cached:
            return cached
        data = self._throttled_get(f"{self.base_url}/asic.json")
        if data:
            self.cache.set("asic_index", data)
        return data

    def get_coin_profitability(
        self,
        coin_id: int,
        hashrate: float,
        hashrate_unit: str,
        wattage: int,
        electricity_cost: float,
        pool_fee: float = 0.0,
    ) -> dict | None:
        """Fetch profitability for a specific coin with custom miner params."""
        raw_hr = hashrate * HASHRATE_MULTIPLIERS.get(hashrate_unit, 1)
        cache_key = f"coin_{coin_id}_{raw_hr}_{wattage}_{electricity_cost}"
        cached = self.cache.get(cache_key, self.ttl)
        if cached:
            return cached

        url = f"{self.base_url}/coins/{coin_id}.json"
        params = {
            "hr": raw_hr,
            "p": wattage,
            "fee": pool_fee,
            "cost": electricity_cost,
            "cost_currency": "USD",
            "hcost": 0.0,
            "span": "24h",
        }
        data = self._throttled_get(url, params)
        if data:
            self.cache.set(cache_key, data)
        return data

    def get_profitability_for_miner(
        self, miner: dict, location: dict, coin_mappings: dict
    ) -> list[dict]:
        """Query all relevant coins for a miner's algorithm, return sorted results."""
        algorithm = miner.get("algorithm", "")
        coins = coin_mappings.get(algorithm, [])
        if not coins:
            return []

        results = []
        for coin_info in coins:
            coin_id = coin_info["coin_id"]
            data = self.get_coin_profitability(
                coin_id=coin_id,
                hashrate=miner["hashrate"],
                hashrate_unit=miner["hashrate_unit"],
                wattage=miner["wattage"],
                electricity_cost=location["electricity_cost_kwh"],
                pool_fee=0.0,
            )
            if data and "revenue" in data:
                results.append({
                    "coin_id": coin_id,
                    "coin_name": data.get("name", coin_info["name"]),
                    "tag": data.get("tag", coin_info["tag"]),
                    "algorithm": data.get("algorithm", algorithm),
                    "daily_revenue": float(data.get("revenue", 0) or 0),
                    "daily_electricity": float(data.get("cost", 0) or 0),
                    "daily_profit": float(data.get("profit", 0) or 0),
                    "estimated_rewards": data.get("estimated_rewards", "0"),
                    "btc_revenue": float(data.get("btc_revenue", 0) or 0),
                    "exchange_rate": float(data.get("exchange_rate", 0) or 0),
                    "difficulty": data.get("difficulty", 0),
                    "nethash": data.get("nethash", ""),
                })

        results.sort(key=lambda x: x["daily_profit"], reverse=True)
        return results

    def get_all_coin_names(self) -> list[dict]:
        """Get simplified coin list for autocomplete."""
        coins_data = self.get_coins_index()
        asic_data = self.get_asic_index()
        result = []
        seen = set()
        for source in [coins_data, asic_data]:
            if not source or "coins" not in source:
                continue
            for name, info in source["coins"].items():
                coin_id = info.get("id")
                if coin_id and coin_id not in seen:
                    seen.add(coin_id)
                    result.append({
                        "coin_id": coin_id,
                        "name": name,
                        "tag": info.get("tag", ""),
                        "algorithm": info.get("algorithm", ""),
                    })
        return result
