"""Kryptex pool (public, wallet- or account-keyed).

Checked live 2026-11-08 against pool.kryptex.com (QTC, account id ``krx...``):
- /{coin}/api/v3/miner/workers/{address} -> {results: [{worker, status, scheme,
  avg_hashrate_30m, avg_hashrate_3h, avg_hashrate_24h, valid, stale, invalid, last_share}]}
  Hashrates are decimal strings in H/s. There is NO plain "hashrate" field.
- /{coin}/api/v1/miner/balance/{address} and /miner/chart/{address} return 403
  ("You do not have permission") for Kryptex *account* ids; they only work for raw
  wallet addresses. So balance and reward history are best-effort.
- /{coin}/api/v1/pool/info -> {net_hashrate (H/s), fee, block_reward, block_time}
- /api/v1/coin/{coin}/info -> {daily_emission (coins/day)}
- /api/v1/coin/{coin}/price/chart -> [{timestamp ms, price USD}] hourly, last = latest
Timestamps are in milliseconds.

Expected revenue for a worker = hashrate / net_hashrate x daily_emission x (1 - fee) x price.
"""

import logging
from datetime import datetime, timezone

from fleet import settings
from fleet.adapters.base import Adapter

logger = logging.getLogger(__name__)

API = "https://pool.kryptex.com"
# Prefer the 30-minute average (responsive but not one share's noise), then longer windows
HASHRATE_KEYS = ("avg_hashrate_30m", "avg_hashrate_3h", "avg_hashrate_24h", "hashrate")


def _ms_to_iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()


def worker_hashrate(w: dict) -> float | None:
    for key in HASHRATE_KEYS:
        value = w.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def usd_per_hs_day(pool_info: dict, coin_info: dict, price_usd: float | None) -> float | None:
    """USD per day earned by 1 H/s at the current network hashrate, after pool fee."""
    net = pool_info.get("net_hashrate")
    emission = coin_info.get("daily_emission")
    if not net or not emission or not price_usd:
        return None
    fee = pool_info.get("fee") or 0
    return float(emission) / float(net) * (1 - float(fee)) * float(price_usd)


class KryptexAdapter(Adapter):
    name = "kryptex"
    interval_s = 300

    def is_configured(self) -> bool:
        return bool(settings.KRYPTEX_WALLETS)

    def _best_effort(self, url):
        try:
            return self.get_json(url)
        except Exception as e:
            logger.info("Kryptex optional call failed (%s): %s", url.rsplit("/", 2)[-2], e)
            return None

    def _coin_rate(self, coin: str) -> dict | None:
        pool_info = self._best_effort(f"{API}/{coin}/api/v1/pool/info") or {}
        coin_info = self._best_effort(f"{API}/api/v1/coin/{coin}/info") or {}
        chart = self._best_effort(f"{API}/api/v1/coin/{coin}/price/chart") or []
        latest = max(chart, key=lambda p: p.get("timestamp") or 0) if chart else {}
        price = latest.get("price")
        rate = usd_per_hs_day(pool_info, coin_info, price)
        if rate is None:
            return None
        return {
            "usd_per_hs_day": rate,
            "price_usd": price,
            "price_at": _ms_to_iso(latest.get("timestamp")),
            "net_hashrate_hs": pool_info.get("net_hashrate"),
            "daily_emission": coin_info.get("daily_emission"),
            "fee": pool_info.get("fee"),
        }

    def poll(self) -> dict:
        machines, accounts, rates = [], {}, {}
        for coin, address in settings.KRYPTEX_WALLETS:
            coin = coin.lower()
            # The workers call is the one that must work; let it raise
            workers = self.get_json(f"{API}/{coin}/api/v3/miner/workers/{address}")
            for w in workers.get("results") or []:
                hs = worker_hashrate(w)
                online = (w.get("status") or "").lower() == "online"
                running = online and (hs or 0) > 0
                machines.append({
                    "machine_key": f"kryptex:{coin}:{w.get('worker')}",
                    "source": self.name,
                    "name": w.get("worker") or "(unnamed)",
                    "algorithm": coin,
                    "running": running,
                    "status": "hashing" if running else (w.get("status") or "no hashrate"),
                    "hashrate_hs": hs,
                    "observed_at": _ms_to_iso(w.get("last_share")),
                    "extra": {
                        "scheme": w.get("scheme"),
                        "valid": w.get("valid"), "stale": w.get("stale"), "invalid": w.get("invalid"),
                        "avg_hashrate_24h_hs": worker_hashrate({"hashrate": w.get("avg_hashrate_24h")}),
                        "agent": w.get("agent"),
                    },
                })
            if coin not in rates:
                rate = self._coin_rate(coin)
                if rate:
                    rates[coin] = rate
            accounts[f"kryptex:{coin}:{address[-6:]}"] = {
                "coin": coin,
                # 403 for krx account ids; present only for raw wallet addresses
                "balance": self._best_effort(f"{API}/{coin}/api/v1/miner/balance/{address}"),
                "daily_rewards": self._best_effort(
                    f"{API}/{coin}/api/v1/miner/reward-chart/{address}") or [],
            }
        accounts["kryptex:rates"] = {"rates": rates}
        return {"machines": machines, "accounts": accounts}
