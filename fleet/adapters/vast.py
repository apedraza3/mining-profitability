"""Vast.ai host account: your rental machines and earnings history. Read-only.

Endpoints as used by the official CLI (github.com/vast-ai/vast-cli, vast.py):
- GET {API}/machines?owner=me -> {"machines": [{id, hostname, gpu_name, num_gpus,
  listed_gpu_cost, gpu_occupancy, reliability2, num_reports, ...}]}   ("show machines")
- GET {API}/users/me/machine-earnings?owner=me&sday=<days since epoch>&eday=<...>
  ("show earnings"; sday/eday are float days since 1970-01-01 UTC)
Header "Authorization: Bearer <key>". Use a key restricted to machine_read +
billing_read (vastai create api-key --permission_file ...): it can't list, unlist,
reprice or spend.

Vast doesn't publish the earnings response schema, so the raw response is kept (trimmed)
and parsed tolerantly: any per-day / per-machine lists of numeric *earn* fields are summed.
"""

import json
import time

from fleet import settings
from fleet.adapters.base import Adapter

API = "https://console.vast.ai/api/v0"
DAY_S = 86400.0
KEEP_FIELDS = ("id", "hostname", "gpu_name", "num_gpus", "listed", "listed_gpu_cost",
               "min_bid_price", "gpu_occupancy", "reliability2", "verification",
               "num_reports", "timeout", "machine_id")
RAW_LIMIT = 20000


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def earning_total(entry: dict) -> float | None:
    """Sum of the numeric fields that look like earnings (gpu/storage/bandwidth)."""
    lower = {k.lower(): v for k, v in entry.items()}
    if "total" in lower:
        return _num(lower["total"])
    vals = [_num(v) for k, v in lower.items() if "earn" in k or k in ("gpu", "sto", "bwu", "bwd")]
    vals = [v for v in vals if v is not None]
    return sum(vals) if vals else None


def rented_gpus(occupancy) -> int | None:
    """gpu_occupancy is one token per GPU; 'x' means idle in the CLI's table."""
    if not isinstance(occupancy, str) or not occupancy.strip():
        return None
    return sum(1 for tok in occupancy.split() if tok.lower() != "x")


class VastAdapter(Adapter):
    name = "vast"
    interval_s = 300

    def is_configured(self) -> bool:
        return bool(settings.VAST_API_KEY)

    def _get(self, path, params):
        return self.get_json(f"{API}{path}", params=params,
                             headers={"Authorization": f"Bearer {settings.VAST_API_KEY}"})

    def poll(self) -> dict:
        machines_raw = (self._get("/machines", {"owner": "me"}) or {}).get("machines") or []
        today = time.time() / DAY_S
        earnings = self._get("/users/me/machine-earnings",
                             {"owner": "me", "sday": today - 7, "eday": today})

        machines = []
        for m in machines_raw:
            name = m.get("hostname") or f"vast-{m.get('id')}"
            rented = rented_gpus(m.get("gpu_occupancy"))
            total = m.get("num_gpus")
            status = ("not rented" if rented == 0 else
                      f"{rented}/{total} GPUs rented" if rented is not None else "listed")
            machines.append({
                "machine_key": f"vast:{m.get('id')}",
                "source": self.name,
                "name": name,
                "kind": "rental_server",
                "location": None,           # the view takes it from inventory when matched
                "running": None,
                "status": status,
                "extra": {k: m.get(k) for k in KEEP_FIELDS if k in m} | {"rented_gpus": rented},
            })

        per_day, per_machine = [], {}
        if isinstance(earnings, dict):
            for key, val in earnings.items():
                if not isinstance(val, list):
                    continue
                for entry in val:
                    if not isinstance(entry, dict):
                        continue
                    amount = earning_total(entry)
                    if amount is None:
                        continue
                    mid = entry.get("machine_id") or entry.get("machid")
                    if mid is not None and "machine" in key.lower():
                        per_machine[str(mid)] = per_machine.get(str(mid), 0) + amount
                    elif "day" in key.lower():
                        per_day.append({"day": entry.get("day"), "usd": amount})
        raw = json.dumps(earnings)[:RAW_LIMIT]
        accounts = {"vast:earnings": {
            "window_days": 7,
            "last_7d_usd": round(sum(d["usd"] for d in per_day), 2) if per_day else None,
            "per_day": per_day[-14:],
            "per_machine_7d_usd": per_machine,
            "raw": raw,
            "note": "Parsed from Vast's undocumented earnings response; check raw if a total is missing.",
        }}
        return {"machines": machines, "accounts": accounts}
