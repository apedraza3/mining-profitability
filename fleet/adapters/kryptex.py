"""Kryptex pool (public, wallet-keyed). Verified against pool.kryptex.com/openapi.yaml:
- /{coin}/api/v3/miner/workers/{address} -> {count, results: [{worker, scheme, hashrate, shares, last_share}]}
- /{coin}/api/v1/miner/balance/{address} -> {total, threshold, reached_pct, unconfirmed, confirmed}
- /{coin}/api/v1/miner/reward-chart/{address} -> [{date, reward}] (31 days; actual credited reward)
Hashrate is in H/s (pool/info for btc reads ~7.2e18 against a ~1.1e21 network, checked live).
Timestamps are in milliseconds.
"""

from datetime import datetime, timezone

from fleet import settings
from fleet.adapters.base import Adapter

API = "https://pool.kryptex.com"


def _ms_to_iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(microsecond=0).isoformat()


class KryptexAdapter(Adapter):
    name = "kryptex"
    interval_s = 300

    def is_configured(self) -> bool:
        return bool(settings.KRYPTEX_WALLETS)

    def poll(self) -> dict:
        machines, accounts = [], {}
        for coin, address in settings.KRYPTEX_WALLETS:
            coin = coin.lower()
            workers = self.get_json(f"{API}/{coin}/api/v3/miner/workers/{address}")
            for w in workers.get("results") or []:
                hs = w.get("hashrate")
                machines.append({
                    "machine_key": f"kryptex:{coin}:{w.get('worker')}",
                    "source": self.name,
                    "name": w.get("worker") or "(unnamed)",
                    "algorithm": coin,
                    "running": (hs or 0) > 0,
                    "status": "hashing" if (hs or 0) > 0 else "no hashrate",
                    "hashrate_hs": float(hs) if hs is not None else None,
                    "observed_at": _ms_to_iso(w.get("last_share")),
                    "extra": {"scheme": w.get("scheme"), "shares": w.get("shares")},
                })
            balance = self.get_json(f"{API}/{coin}/api/v1/miner/balance/{address}")
            rewards = self.get_json(f"{API}/{coin}/api/v1/miner/reward-chart/{address}")
            accounts[f"kryptex:{coin}:{address[-6:]}"] = {
                "coin": coin,
                "balance": balance,
                "daily_rewards": rewards,  # actual credited reward per day, in coin
            }
        return {"machines": machines, "accounts": accounts}
