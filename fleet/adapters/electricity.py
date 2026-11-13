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
from datetime import datetime, timedelta, timezone

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
            "peak_basis": bill.get("peak_basis"),
            "bill": {k: bill.get(k) for k in ("total_estimate", "demand_charge", "energy_charge",
                                                "energy_kwh", "period_start_utc", "hours_measured",
                                                "hours_in_period", "effective_rate_per_kwh")},
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
        # Last 24 complete hours. hour_start is stored as "YYYY-MM-DD HH:00:00" (space, UTC)
        # and compared as text, so the bounds must use the same format.
        this_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        fmt = "%Y-%m-%d %H:00:00"
        hourly = self._optional(
            f"/api/hourly?start={(this_hour - timedelta(hours=24)).strftime(fmt)}"
            f"&end={(this_hour - timedelta(hours=1)).strftime(fmt)}")
        if isinstance(hourly, list) and hourly and "electricity:realtime" in accounts:
            def kwh(field):
                return round(sum(float(h.get(field) or 0) for h in hourly) / 1000, 3)
            accounts["electricity:realtime"]["last24h"] = {
                "hours": len(hourly),
                "solar_kwh": kwh("solar_production_wh"),
                "house_kwh": kwh("house_consumption_wh"),
                "crypto_kwh": kwh("crypto_mining_wh"),
                "net_grid_kwh": kwh("net_grid_wh"),
            }
        # Every hour of the current billing period, for the rate-plan comparison (fleet/tariff.py)
        start = (bill.get("period_start_utc") or "")[:19]
        if start:
            period = self._optional(f"/api/hourly?start={start.replace('T', ' ')}"
                                    f"&end={this_hour.strftime(fmt)}")
            if isinstance(period, list):
                accounts["electricity:period"] = {
                    "period_start_utc": start, "hours_in_period": bill.get("hours_in_period"),
                    "wpca": wpca, "tax": tax, "house_peak_kw": bill.get("peak_demand_kw"),
                    "hours": [[h.get("hour_start"), h.get("net_grid_wh")] for h in period],
                }
        return {"machines": [], "accounts": accounts}
