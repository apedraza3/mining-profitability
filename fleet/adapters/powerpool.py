"""PowerPool: public pay rates (no key) and observer workers/balance (observer key).

Verified: /api/pool returns pools.<algo>.{BTCpayRate, payRateUnits, fee, hashrate_units}
plus prices (checked live 2026-10-18). Pay rates are BTC per day per unit of hashrate.
Observer /observer/mining/workers fields match the existing powerpool_service.py.
Observer balance/earnings shapes are undocumented: stored raw, best-effort.
"""

import logging

from fleet import settings
from fleet.adapters.base import Adapter
from fleet.units import pay_rate_per_hs, to_hs

logger = logging.getLogger(__name__)

API = "https://api.powerpool.io"


def normalize_algo(name: str) -> str:
    return (name or "").lower().replace("-", "").replace("_", "").replace(" ", "")


class PowerPoolAdapter(Adapter):
    name = "powerpool"
    interval_s = 120

    def poll(self) -> dict:
        accounts = {}
        machines = []

        pool = self.get_json(f"{API}/api/pool")
        rates = {}
        for algo, p in (pool.get("pools") or {}).items():
            per_hs = pay_rate_per_hs(p.get("BTCpayRate") or 0, p.get("payRateUnits"))
            if per_hs is not None:
                rates[normalize_algo(algo)] = {
                    "btc_per_hs_day": per_hs,
                    "fee": p.get("fee"),
                    "units": p.get("payRateUnits"),
                }
        accounts["powerpool:payrates"] = {"rates": rates, "prices": pool.get("prices") or {}}

        key = settings.POWERPOOL_OBSERVER_KEY
        if key:
            data = self.get_json(f"{API}/observer/mining/workers", params={"observer_key": key})
            for w in data.get("workers", []):
                full = w.get("name", "")
                short = full.split(".", 1)[1] if "." in full else full
                hs = to_hs(w.get("hashrate"), w.get("hashrate_units", ""))
                machines.append({
                    "machine_key": f"powerpool:{short}:{normalize_algo(w.get('algorithm', ''))}",
                    "source": self.name,
                    "name": short,
                    "kind": "asic",
                    "algorithm": normalize_algo(w.get("algorithm", "")),
                    "running": (hs or 0) > 0,
                    "status": "hashing" if (hs or 0) > 0 else "no hashrate",
                    "hashrate_hs": hs,
                    "extra": {
                        "hashrate_avg_hs": to_hs(w.get("hashrate_avg"), w.get("hashrate_avg_units", "")),
                        "valid_shares": w.get("valid_shares"),
                        "invalid_shares": w.get("invalid_shares"),
                        "stale_shares": w.get("stale_shares"),
                    },
                })
            # Undocumented shapes: keep raw, don't fail the source if they break
            for route, account in (("/observer/revenue/balance", "powerpool:balance"),
                                   ("/observer/revenue/earnings", "powerpool:earnings")):
                try:
                    accounts[account] = {
                        "raw": self.get_json(f"{API}{route}", params={"observer_key": key}),
                        "note": "undocumented response shape; shown raw",
                    }
                except Exception as e:
                    logger.warning("PowerPool %s failed: %s", route, e)

        return {"machines": machines, "accounts": accounts}
