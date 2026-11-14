"""Clore.ai host account: rental status per server and wallet balances.

Verified against docs.clore.ai (for-hosts/api) and the clore-ai SDK source:
- Base https://api.clore.ai/v1, header "auth: <key>", ~1 req/s; body code 5 = rate limited
- GET my_servers -> {servers: [{name, online, connected, visibility, pricing, ...}]}
  rental_state (none|partial|full), rented_gpus, total_gpus, rented_price_by_currency and
  remaining_time are present only while an order is active.
- GET wallets -> {wallets: [{name, deposit, balance, withdrawal_fee}]}
There is no earnings-history endpoint. rented_price_by_currency is the renter's price per
day, before Clore's host fees. Secret fields (e.g. init_token) are never stored.
"""

import logging
import time

from fleet import settings
from fleet.adapters.base import Adapter, SourceError

logger = logging.getLogger(__name__)

API = "https://api.clore.ai/v1"
KEEP_FIELDS = ("online", "connected", "visibility", "pricing", "min_spot_pricing",
               "rental_state", "rented_gpus", "total_gpus", "rented_price_by_currency",
               "remaining_time", "gpu_count", "working_properly")


def rental_status(server: dict) -> str:
    if not server.get("online"):
        return "offline"
    state = server.get("rental_state") or "none"
    if state == "full":
        return "fully rented"
    if state == "partial":
        return "partly rented"
    # visibility values aren't enumerated in the docs; it's shown raw in the detail
    return "online, not rented"


class CloreAdapter(Adapter):
    name = "clore"
    interval_s = 60

    def is_configured(self) -> bool:
        return bool(settings.CLORE_API_KEY)

    def _call(self, endpoint):
        data = self.get_json(f"{API}/{endpoint}", headers={"auth": settings.CLORE_API_KEY})
        code = data.get("code", 0)
        if code == 5:
            raise SourceError("Clore rate limit (code 5)")
        if code not in (0, None):
            raise SourceError(f"Clore error code {code}")
        return data

    _price = (0.0, None)  # (monotonic time fetched, CLORE price in USD)

    def _clore_usd(self):
        """CLORE token price for rentals paid in CLORE. CoinGecko public API, cached 10 min;
        best-effort (keeps the last price, or None, if it fails)."""
        fetched, price = CloreAdapter._price
        if price is None or time.monotonic() - fetched > 600:
            try:
                data = self.get_json("https://api.coingecko.com/api/v3/simple/price",
                                     params={"ids": "clore-ai", "vs_currencies": "usd"}, timeout=10)
                price = float(data["clore-ai"]["usd"])
                CloreAdapter._price = (time.monotonic(), price)
            except Exception as e:
                logger.info("CLORE price unavailable: %s", e)
        return price

    def poll(self) -> dict:
        servers = self._call("my_servers").get("servers") or []
        time.sleep(1.1)
        wallets = self._call("wallets").get("wallets") or []

        machines = []
        for s in servers:
            status = rental_status(s)
            name = s.get("name") or str(s.get("id"))
            machines.append({
                "machine_key": f"clore:{name}",
                "source": self.name,
                "name": name,
                "kind": "rental_server",
                "location": "clore",
                "running": bool(s.get("online")),
                "status": status,
                "extra": {k: s.get(k) for k in KEEP_FIELDS if k in s},
            })
        accounts = {"clore:wallets": {
            "wallets": [{"name": w.get("name"), "balance": w.get("balance")} for w in wallets],
            "clore_usd": self._clore_usd(),
        }}
        return {"machines": machines, "accounts": accounts}
