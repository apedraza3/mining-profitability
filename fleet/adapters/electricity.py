"""Home electricity from the electricity-dashboard (CORE Schedule C rates + live meters).

Cached as account rows so the web app never has to call out. If the dashboard is down,
the last good values stay in the database and the source shows as failing.

- /api/settings + /api/bill-estimate -> rates (required; the source fails without them)
- /api/realtime -> collector's latest merged reading: crypto_mining_w (Emporia crypto
  circuits), solar_production_w and house_consumption_w (PVS6), net_grid_w, timestamp
  (naive UTC). Best-effort.
- /api/circuits -> per-circuit Emporia watts. The collector cache uses {channel_num,
  name, usage_w}; the DB fallback uses {channel_num, channel_name, usage_w, is_crypto}.
"""

import logging

from fleet import settings
from fleet.adapters.base import Adapter

logger = logging.getLogger(__name__)


class ElectricityAdapter(Adapter):
    name = "electricity"
    interval_s = 120

    def _optional(self, path):
        try:
            return self.get_json(f"{settings.ELECTRICITY_API_URL}{path}", timeout=5)
        except Exception as e:
            logger.info("electricity %s unavailable: %s", path, e)
            return None

    def poll(self) -> dict:
        s = self.get_json(f"{settings.ELECTRICITY_API_URL}/api/settings", timeout=5)
        energy = float(s.get("energy_rate") or 0)
        wpca = float(s.get("wpca_rate") or 0)
        tax = float(s.get("tax_rate") or 0)
        demand = float(s.get("demand_rate") or 0)
        bill = self._optional("/api/bill-estimate") or {}
        accounts = {"electricity:rates": {
            "marginal_energy_rate": round((energy + wpca) * (1 + tax), 5),
            "demand_rate_with_tax": round(demand * (1 + tax), 4),
            "house_peak_kw": bill.get("peak_demand_kw"),
        }}

        rt = self._optional("/api/realtime")
        if rt and rt.get("status") == "ok":
            circuits = []
            for c in (self._optional("/api/circuits") or {}).get("circuits") or []:
                circuits.append({
                    "channel": c.get("channel_num"),
                    "name": c.get("name") or c.get("channel_name") or "",
                    "watts": c.get("usage_w"),
                    "is_crypto": bool(c.get("is_crypto")) if "is_crypto" in c else None,
                })
            accounts["electricity:realtime"] = {
                "crypto_mining_w": rt.get("crypto_mining_w"),
                "solar_w": rt.get("solar_production_w"),
                "house_w": rt.get("house_consumption_w"),
                "net_grid_w": rt.get("net_grid_w"),
                "emporia_total_w": rt.get("emporia_total_w"),
                "measured_at": rt.get("timestamp"),
                "circuits": circuits,
            }
        return {"machines": [], "accounts": accounts}
