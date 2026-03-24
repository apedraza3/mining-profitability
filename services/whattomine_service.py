import time
import logging

import requests

from services.cache_manager import CacheManager
import config

logger = logging.getLogger(__name__)


def _parse_dollar(val) -> float:
    """Parse WhatToMine dollar strings like '$8.33' or '-$1.05' into floats."""
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def _parse_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


class WhatToMineService:
    def __init__(self, cache: CacheManager):
        self.cache = cache
        self.base_url = config.WHATTOMINE_BASE_URL
        self.ttl = config.WHATTOMINE_CACHE_TTL
        self._last_request_time = 0

    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://whattomine.com/",
    }

    def _throttled_get(self, url: str, params: dict = None) -> dict | None:
        elapsed = time.time() - self._last_request_time
        if elapsed < config.WHATTOMINE_REQUEST_DELAY:
            time.sleep(config.WHATTOMINE_REQUEST_DELAY - elapsed)

        for attempt in range(3):
            try:
                resp = requests.get(
                    url, params=params, headers=self._HEADERS, timeout=15
                )
                self._last_request_time = time.time()
                if resp.status_code == 403:
                    wait = config.WHATTOMINE_REQUEST_DELAY * (attempt + 2)
                    logger.warning("WhatToMine 403 (rate limit), waiting %.1fs...", wait)
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.RequestException as e:
                logger.error("WhatToMine request failed: %s", e)
                return None
        logger.error("WhatToMine 403 persisted after retries: %s", url)
        return None

    def get_coins_index(self) -> dict | None:
        """Fetch the full GPU-mineable coins list."""
        cached = self.cache.get("coins_index", self.ttl)
        if cached:
            return cached
        data = self._throttled_get(f"{self.base_url}/coins.json")
        if data:
            self.cache.set("coins_index", data)
        return data

    def get_asic_index(self) -> dict | None:
        """Fetch the full ASIC-mineable coins list."""
        cached = self.cache.get("asic_index", self.ttl)
        if cached:
            return cached
        data = self._throttled_get(f"{self.base_url}/asic.json")
        if data:
            self.cache.set("asic_index", data)
        return data

    def get_coin_reference_data(self, coin_id: int) -> dict | None:
        """Fetch reference data for a coin with hr=1, p=0, cost=0.
        Revenue scales linearly with hashrate, so we cache once per coin
        instead of once per miner — dramatically reduces API calls."""
        cache_key = f"coin_ref_{coin_id}"
        cached = self.cache.get(cache_key, self.ttl)
        if cached:
            return cached

        url = f"{self.base_url}/coins/{coin_id}.json"
        params = {
            "hr": 1,
            "p": 0,
            "fee": 0,
            "cost": 0,
            "cost_currency": "USD",
            "hcost": 0.0,
            "span": "24h",
        }
        data = self._throttled_get(url, params)
        if data:
            self.cache.set(cache_key, data)
        return data

    def get_profitability_for_miner(
        self, miner: dict, location: dict, coin_mappings: dict,
        primary_only: bool = True,
    ) -> list[dict]:
        """Query relevant coins for a miner's algorithm, return sorted results.
        Uses reference caching: fetches once per coin (hr=1), scales per miner."""
        algorithm = miner.get("algorithm", "")
        coins = coin_mappings.get(algorithm, [])
        if not coins:
            return []

        if primary_only:
            coins = coins[:1]

        hashrate = miner.get("hashrate", 0)
        wattage = miner.get("wattage", 0)
        elec_cost = location.get("electricity_cost_kwh", 0.10)

        results = []
        for coin_info in coins:
            coin_id = coin_info["coin_id"]
            ref = self.get_coin_reference_data(coin_id)
            if ref and "revenue" in ref:
                ref_revenue = _parse_dollar(ref.get("revenue"))
                daily_revenue = ref_revenue * hashrate
                daily_electricity = (wattage * 24 / 1000) * elec_cost
                daily_profit = daily_revenue - daily_electricity

                ref_rewards = ref.get("estimated_rewards", "0")
                try:
                    scaled_rewards = str(round(float(ref_rewards) * hashrate, 8))
                except (ValueError, TypeError):
                    scaled_rewards = "0"

                results.append({
                    "coin_id": coin_id,
                    "coin_name": ref.get("name", coin_info["name"]),
                    "tag": ref.get("tag", coin_info["tag"]),
                    "algorithm": ref.get("algorithm", algorithm),
                    "daily_revenue": round(daily_revenue, 4),
                    "daily_electricity": round(daily_electricity, 4),
                    "daily_profit": round(daily_profit, 4),
                    "estimated_rewards": scaled_rewards,
                    "btc_revenue": round(
                        _parse_float(ref.get("btc_revenue")) * hashrate, 8
                    ),
                    "exchange_rate": _parse_float(ref.get("exchange_rate")),
                    "difficulty": ref.get("difficulty", 0),
                    "nethash": ref.get("nethash", ""),
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
