"""Foreman (hosted miners at Metered Mining).

Verified against the official Foreman API 2.0.0 spec (swaggerhub foremanmining):
- Auth header: "Authorization: Token <key>"
- GET /clients/{clientId}/miners -> {limit, offset, total, results: [miner]}
  miner: id, name, type.name, ip, mac, serial, lastUpdated, seen, active,
         status (pending|disabled|okay|warn|fail), powerMode, firmware.author, pools
- GET /clients/{clientId}/miners/summary -> [{id, hashrate (H/s), powerMode, workers, lastUpdated}]
The spec defines live power/temps (metricInfo) but exposes no path that returns them,
so watts are left for the view to estimate. Endpoints are rate limited to ~1 req/s.
"""

import time

from fleet import settings
from fleet.adapters.base import Adapter

API = "https://api.foreman.mn/api/v2"


class ForemanAdapter(Adapter):
    name = "foreman"
    interval_s = 120

    def is_configured(self) -> bool:
        return bool(settings.FOREMAN_API_KEY and settings.FOREMAN_CLIENT_ID)

    def poll(self) -> dict:
        headers = {"Authorization": f"Token {settings.FOREMAN_API_KEY}"}
        base = f"{API}/clients/{settings.FOREMAN_CLIENT_ID}"

        # The spec returns limit/offset/total but doesn't document request params, so
        # stop as soon as a page repeats or nothing new arrives.
        miners, seen = [], set()
        offset = 0
        for _ in range(50):
            page = self.get_json(f"{base}/miners", headers=headers, params={"offset": offset})
            new = [m for m in (page.get("results") or []) if m.get("id") not in seen]
            if not new:
                break
            seen.update(m.get("id") for m in new)
            miners.extend(new)
            offset += len(new)
            if offset >= (page.get("total") or 0):
                break
            time.sleep(1.1)

        time.sleep(1.1)
        summary = self.get_json(f"{base}/miners/summary", headers=headers)
        hashrate = {s.get("id"): s.get("hashrate") for s in (summary or [])}

        machines = []
        for m in miners:
            hs = hashrate.get(m.get("id"))
            status = m.get("status")
            if hs is not None:
                running = hs > 0
            elif status in ("okay", "warn"):
                running = True
            elif status == "fail":
                running = False
            else:
                running = None
            machines.append({
                "machine_key": f"foreman:{m.get('id')}",
                "source": self.name,
                "name": m.get("name") or str(m.get("id")),
                "kind": "asic",
                "location": "metered-mining",
                "running": running,
                "status": status,
                "hashrate_hs": float(hs) if hs is not None else None,
                "observed_at": m.get("lastUpdated"),
                "extra": {
                    "model": (m.get("type") or {}).get("name"),
                    "active": m.get("active"),
                    "power_mode": m.get("powerMode"),
                    "firmware": (m.get("firmware") or {}).get("author"),
                    "serial": m.get("serial"),
                    "mac": m.get("mac"),
                },
            })
        return {"machines": machines, "accounts": {}}
