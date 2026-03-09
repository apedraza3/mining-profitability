"""PowerPool observer API integration for real-time worker monitoring."""

import logging

import requests

logger = logging.getLogger(__name__)

POWERPOOL_API_BASE = "https://api.powerpool.io"
POWERPOOL_CACHE_TTL = 120  # 2 minutes — worker status should be fresh


class PowerPoolService:
    def __init__(self, observer_key, cache):
        self.observer_key = observer_key
        self.cache = cache

    def is_configured(self):
        return bool(self.observer_key)

    def get_workers(self):
        """Fetch all workers from PowerPool observer API."""
        if not self.is_configured():
            return []

        cached = self.cache.get("workers", POWERPOOL_CACHE_TTL)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{POWERPOOL_API_BASE}/observer/mining/workers",
                params={"observer_key": self.observer_key},
                timeout=10,
            )
            if not resp.ok:
                logger.error("PowerPool API error: %s", resp.status_code)
                return []

            data = resp.json()
            workers = data.get("workers", [])

            for w in workers:
                full_name = w.get("name", "")
                # Remove "USERNAME." prefix
                if "." in full_name:
                    w["short_name"] = full_name.split(".", 1)[1]
                else:
                    w["short_name"] = full_name
                # Online = hashrate > 0
                w["online"] = w.get("hashrate", 0) > 0

            self.cache.set("workers", workers)
            return workers
        except Exception as e:
            logger.error("PowerPool fetch error: %s", e)
            return []

    def get_mining_overview(self):
        """Fetch overall mining stats per algorithm."""
        if not self.is_configured():
            return {}

        cached = self.cache.get("mining_overview", POWERPOOL_CACHE_TTL)
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{POWERPOOL_API_BASE}/observer/mining",
                params={"observer_key": self.observer_key},
                timeout=10,
            )
            if not resp.ok:
                return {}

            data = resp.json()
            self.cache.set("mining_overview", data)
            return data
        except Exception as e:
            logger.error("PowerPool overview error: %s", e)
            return {}

    def get_revenue(self):
        """Fetch earnings/balance info."""
        if not self.is_configured():
            return {}

        cached = self.cache.get("revenue", 300)  # 5 min cache
        if cached is not None:
            return cached

        try:
            resp = requests.get(
                f"{POWERPOOL_API_BASE}/observer/revenue/balance",
                params={"observer_key": self.observer_key},
                timeout=10,
            )
            if not resp.ok:
                return {}

            data = resp.json()
            self.cache.set("revenue", data)
            return data
        except Exception as e:
            logger.error("PowerPool revenue error: %s", e)
            return {}

    def match_worker_to_miner(self, miner, workers):
        """Match an inventory miner to a PowerPool worker.

        Priority:
        1. Explicit powerpool_worker_key override
        2. Exact name match
        3. Normalized fuzzy match (remove underscores, hyphens)
        """
        override = miner.get("powerpool_worker_key", "").strip()
        miner_name = miner.get("name", "")

        # 1. Explicit override
        if override:
            for w in workers:
                if w["short_name"].lower() == override.lower():
                    return w

        # 2. Exact match
        for w in workers:
            if w["short_name"].lower() == miner_name.lower():
                return w

        # 3. Normalized containment match
        def normalize(s):
            return s.lower().replace("_", "").replace("-", "").replace("+", "plus")

        norm_miner = normalize(miner_name)
        for w in workers:
            norm_worker = normalize(w["short_name"])
            if norm_miner in norm_worker or norm_worker in norm_miner:
                return w

        return None

    def get_all_worker_statuses(self, miners):
        """Return a dict of miner_id -> worker status for all inventory miners."""
        workers = self.get_workers()
        if not workers:
            return {}

        result = {}
        for miner in miners:
            match = self.match_worker_to_miner(miner, workers)
            if match:
                result[miner["id"]] = {
                    "online": match["online"],
                    "worker_name": match["short_name"],
                    "hashrate": round(match.get("hashrate", 0), 2),
                    "hashrate_units": match.get("hashrate_units", ""),
                    "hashrate_avg": round(match.get("hashrate_avg", 0), 2),
                    "hashrate_avg_units": match.get("hashrate_avg_units", ""),
                    "valid_shares": match.get("valid_shares", 0),
                    "invalid_shares": match.get("invalid_shares", 0),
                    "stale_shares": match.get("stale_shares", 0),
                    "algorithm": match.get("algorithm", ""),
                    "blocks_found": match.get("blocks", 0),
                }
        return result

    def get_unmatched_workers(self, miners):
        """Return PowerPool workers that don't match any inventory miner."""
        workers = self.get_workers()
        matched_ids = set()
        for miner in miners:
            match = self.match_worker_to_miner(miner, workers)
            if match:
                matched_ids.add(match.get("id"))

        return [w for w in workers if w.get("id") not in matched_ids]

    def get_cache_age(self):
        return self.cache.get_age_seconds("workers")
