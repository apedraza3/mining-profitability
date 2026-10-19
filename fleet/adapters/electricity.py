"""Home electricity rates from the electricity-dashboard (CORE Schedule C).

Cached as an account row so the web app never has to call out. If the dashboard is down,
the last good rates stay in the database and the source shows as failing.
"""

from fleet import settings
from fleet.adapters.base import Adapter


class ElectricityAdapter(Adapter):
    name = "electricity"
    interval_s = 600

    def poll(self) -> dict:
        s = self.get_json(f"{settings.ELECTRICITY_API_URL}/api/settings", timeout=5)
        energy = float(s.get("energy_rate") or 0)
        wpca = float(s.get("wpca_rate") or 0)
        tax = float(s.get("tax_rate") or 0)
        demand = float(s.get("demand_rate") or 0)
        bill = self.get_json(f"{settings.ELECTRICITY_API_URL}/api/bill-estimate", timeout=5)
        return {"machines": [], "accounts": {"electricity:rates": {
            "marginal_energy_rate": round((energy + wpca) * (1 + tax), 5),
            "demand_rate_with_tax": round(demand * (1 + tax), 4),
            "house_peak_kw": bill.get("peak_demand_kw"),
        }}}
