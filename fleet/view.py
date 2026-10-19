"""Build the "fleet now" view from telemetry.db plus the inventory. Makes no outbound calls.

Every money figure says where it came from:
- expected revenue: pool-side hashrate x PowerPool's published pay rate for the algorithm
- watts: measured/reported by a source, else inventory nameplate ("estimated")
- power cost: home = marginal rate (energy + WPCA + tax); hosted = location rate
- demand share (home only): kW x demand rate incl. tax / 30.4 days, shown separately
"""

import re
from datetime import datetime, timezone

from fleet import store
from fleet.adapters import all_adapters

# Device sources describe the machine itself; pool sources describe its hashing
DEVICE_PRIORITY = ("lan", "foreman", "clore")
POOL_SOURCES = ("powerpool", "kryptex")
KRYPTEX_COIN_ALGO = {"btc": "sha256", "ltc": "scrypt", "doge": "scrypt", "kas": "kheavyhash"}
DAYS_PER_MONTH = 30.4


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower().replace("+", "plus"))


def norm_algo(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _age_s(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except ValueError:
        return None


def _intervals() -> dict:
    return {a.name: a.interval_s for a in all_adapters()}


def source_health(sources: list[dict]) -> list[dict]:
    intervals = _intervals()
    out = []
    for s in sources:
        if not s["configured"]:
            state = "not configured"
        elif s["consecutive_failures"]:
            state = "error"
        else:
            age = _age_s(s.get("last_success"))
            limit = 3 * intervals.get(s["source"], 300)
            state = "ok" if age is not None and age <= limit else "stale"
        out.append({**s, "state": state})
    return out


def _inventory_index(inventory_miners, inventory_locations):
    locations = {l.get("id"): l for l in inventory_locations or []}
    index = {}
    for m in inventory_miners or []:
        loc = locations.get(m.get("location_id"), {})
        entry = {**m, "_location": loc}
        index[norm(m.get("name"))] = entry
        if m.get("powerpool_worker_key"):
            index[norm(m["powerpool_worker_key"])] = entry
    return index


def build_fleet(inventory_miners=None, inventory_locations=None) -> dict:
    data = store.read_all()
    intervals = _intervals()
    accounts = {a["account_key"]: a for a in data["accounts"]}
    payrates = (accounts.get("powerpool:payrates") or {}).get("data", {})
    rates = payrates.get("rates", {})
    prices = payrates.get("prices", {})
    btc_usd = prices.get("BTC")
    elec = (accounts.get("electricity:rates") or {}).get("data", {})
    home_rate = elec.get("marginal_energy_rate")
    demand_rate = elec.get("demand_rate_with_tax")
    inventory = _inventory_index(inventory_miners, inventory_locations)

    # Group rows from different sources that describe the same machine (by name)
    groups: dict[str, list[dict]] = {}
    for m in data["machines"]:
        key = f"clore:{m['name']}" if m["source"] == "clore" else norm(m["name"])
        groups.setdefault(key, []).append(m)

    rows = []
    for key, members in groups.items():
        by_source = {m["source"]: m for m in members}
        device = next((by_source[s] for s in DEVICE_PRIORITY if s in by_source), None)
        pool = next((by_source[s] for s in POOL_SOURCES if s in by_source), None)
        primary = device or pool
        inv = inventory.get(norm(primary["name"]))

        stale = []
        for m in members:
            age = _age_s(m.get("fetched_at"))
            if age is None or age > 3 * intervals.get(m["source"], 300):
                stale.append(m["source"])

        running = device.get("running") if device and device.get("running") is not None else (
            pool.get("running") if pool else None)
        if device and device["source"] == "clore":
            status = device.get("status")
        elif running is None:
            status = (device or pool).get("status") or "unknown"
        else:
            status = "running" if running else ((device or pool).get("status") or "not running")

        algorithm = None
        if pool:
            algorithm = pool.get("algorithm")
            if pool["source"] == "kryptex":
                algorithm = KRYPTEX_COIN_ALGO.get(algorithm, algorithm)
        if not algorithm and inv:
            algorithm = norm_algo(inv.get("algorithm"))

        # Pool-side hashrate is what actually earns; device hashrate is shown alongside
        hashrate = (pool or {}).get("hashrate_hs")
        if hashrate is None and device:
            hashrate = device.get("hashrate_hs")

        location = (device or {}).get("location") or (inv or {}).get("_location", {}).get("name")
        is_home = (location or "").lower() == "home"

        watts, watts_quality = None, None
        if device and device.get("watts") is not None:
            watts, watts_quality = device["watts"], device.get("watts_quality") or "reported"
        elif running is False:
            watts, watts_quality = 0.0, "assumed off"
        elif inv and inv.get("wattage"):
            watts = float(inv["wattage"]) * (inv.get("quantity") or 1)
            watts_quality = "estimated (nameplate)"

        rate = home_rate if is_home else (inv or {}).get("_location", {}).get("electricity_cost_kwh")

        expected_usd = None
        expected_basis = None
        rate_info = rates.get(algorithm or "")
        if device and device["source"] == "clore":
            expected_basis = "rental price shown in detail (renter price, before Clore fees)"
        elif hashrate and rate_info and btc_usd:
            expected_usd = hashrate * rate_info["btc_per_hs_day"] * btc_usd
            expected_basis = f"PowerPool pay rate ({rate_info['units']}) x pool hashrate"

        power_usd = watts / 1000 * 24 * rate if watts is not None and rate is not None else None
        demand_usd = (watts / 1000 * demand_rate / DAYS_PER_MONTH
                      if is_home and watts and demand_rate else None)
        net_marginal = (expected_usd - power_usd
                        if expected_usd is not None and power_usd is not None else None)
        net_loaded = (net_marginal - (demand_usd or 0) if net_marginal is not None else None)

        rows.append({
            "name": primary["name"],
            "sources": sorted(by_source),
            "kind": (device or pool).get("kind") or "asic",
            "location": location,
            "model": ((device or {}).get("extra") or {}).get("model") or (inv or {}).get("model"),
            "in_inventory": inv is not None,
            "running": running,
            "status": status,
            "algorithm": algorithm,
            "hashrate_hs": hashrate,
            "device_hashrate_hs": (device or {}).get("hashrate_hs"),
            "watts": watts,
            "watts_quality": watts_quality,
            "rate_kwh": rate,
            "expected_usd_day": expected_usd,
            "expected_basis": expected_basis,
            "power_usd_day": power_usd,
            "demand_usd_day": demand_usd,
            "net_marginal_usd_day": net_marginal,
            "net_loaded_usd_day": net_loaded,
            "stale_sources": stale,
            "observed_at": (device or pool).get("observed_at"),
            "detail": {m["source"]: m.get("extra") for m in members},
        })

    rows.sort(key=lambda r: (r["location"] or "zzz", r["name"].lower()))

    def total(field):
        vals = [r[field] for r in rows if r[field] is not None]
        return round(sum(vals), 2) if vals else None

    summary = {
        "machines": len(rows),
        "running": sum(1 for r in rows if r["running"] is True),
        "not_running": sum(1 for r in rows if r["running"] is False),
        "unknown": sum(1 for r in rows if r["running"] is None),
        "expected_usd_day": total("expected_usd_day"),
        "power_usd_day": total("power_usd_day"),
        "demand_usd_day": total("demand_usd_day"),
        "net_marginal_usd_day": total("net_marginal_usd_day"),
        "net_loaded_usd_day": total("net_loaded_usd_day"),
        "rows_missing_cost": sum(1 for r in rows if r["power_usd_day"] is None and r["kind"] != "rental_server"),
        "btc_usd": btc_usd,
        "home_rate_kwh": home_rate,
        "demand_rate_kw_month": demand_rate,
    }

    account_view = []
    for a in data["accounts"]:
        if a["account_key"] in ("powerpool:payrates", "electricity:rates"):
            continue
        d = a["data"]
        item = {"account": a["account_key"], "source": a["source"], "fetched_at": a["fetched_at"], "data": d}
        if a["source"] == "kryptex":
            coin = (d.get("coin") or "").upper()
            last7 = sorted(d.get("daily_rewards") or [], key=lambda p: p.get("date") or 0)[-7:]
            reward7 = sum(p.get("reward") or 0 for p in last7)
            item["actual_last_7d_coin"] = reward7
            item["actual_last_7d_usd"] = reward7 * prices[coin] if coin in prices else None
        account_view.append(item)

    return {
        "generated_at": store.utcnow(),
        "summary": summary,
        "machines": rows,
        "accounts": account_view,
        "sources": source_health(data["sources"]),
    }
