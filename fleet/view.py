"""Build the "fleet now" view from telemetry.db plus the inventory. Makes no outbound calls.

Every money figure says where it came from:
- expected revenue: pool-side hashrate x PowerPool's published pay rate for the algorithm
- watts: measured/reported by a source, else inventory nameplate ("estimated")
- power cost: home = marginal rate (energy + WPCA + tax); hosted = location rate
- demand share (home only): kW x demand rate incl. tax / 30.4 days, shown separately
"""

import json
import logging
import re
from datetime import datetime, timezone

from fleet import settings, store
from fleet.adapters import all_adapters
from fleet.units import to_hs

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


def _match_inventory(index: dict, name: str):
    """Find the inventory entry for a source's machine name.

    Mirrors PowerPoolService.match_worker_to_miner so /fleet and the main dashboard
    agree (e.g. worker "S21Hosted1" -> inventory "S21Hosted"): exact normalized name or
    powerpool_worker_key first, then containment when the shorter name is >= 70% of
    the longer one.
    """
    key = norm(name)
    if not key:
        return None
    if key in index:
        return index[key]
    for inv_key, entry in index.items():
        shorter, longer = sorted([key, inv_key], key=len)
        if shorter and len(shorter) / len(longer) >= 0.7 and shorter in longer:
            return entry
    return None


def _inventory_only_rows(inventory_miners, inventory_locations, matched_ids, rates, btc_usd,
                         home_rate, demand_rate) -> list[dict]:
    """Active inventory machines that no live source reports (e.g. the CircleHash S21+ Hyd,
    which mines to CircleHash's own AntPool account). Shown with clearly labelled estimates
    instead of silently disappearing from the totals."""
    locations = {l.get("id"): l for l in inventory_locations or []}
    out = []
    for inv in inventory_miners or []:
        if inv.get("id") in matched_ids or (inv.get("status") or "active") != "active":
            continue
        loc = locations.get(inv.get("location_id"), {})
        location = loc.get("name")
        is_home = (location or "").lower() == "home"
        qty = inv.get("quantity") or 1
        algorithm = norm_algo(inv.get("algorithm"))
        hs = to_hs(inv.get("hashrate"), inv.get("hashrate_unit") or "")
        hashrate = hs * qty if hs is not None else None
        watts = float(inv["wattage"]) * qty if inv.get("wattage") else None
        rate = home_rate if is_home else loc.get("electricity_cost_kwh")

        expected_usd, expected_basis = None, "no live source and no pay rate for this algorithm"
        rate_info = rates.get(algorithm)
        if hashrate and rate_info and btc_usd:
            expected_usd = hashrate * rate_info["btc_per_hs_day"] * btc_usd
            expected_basis = ("ESTIMATE, no live source: inventory rated hashrate x PowerPool "
                              f"{rate_info['units']} pay rate (check the host's own dashboard)")
        power_usd = watts / 1000 * 24 * rate if watts is not None and rate is not None else None
        demand_usd = (watts / 1000 * demand_rate / DAYS_PER_MONTH
                      if is_home and watts and demand_rate else None)
        net_marginal = (expected_usd - power_usd
                        if expected_usd is not None and power_usd is not None else None)
        out.append({
            "name": inv.get("name"),
            "sources": ["inventory"],
            "kind": "asic",
            "location": location,
            "model": inv.get("model"),
            "in_inventory": True,
            "running": None,
            "status": "no live source",
            "algorithm": algorithm,
            "hashrate_hs": hashrate,
            "device_hashrate_hs": None,
            "watts": watts,
            "watts_quality": "estimated (nameplate)" if watts is not None else None,
            "rate_kwh": rate,
            "expected_usd_day": expected_usd,
            "expected_basis": expected_basis,
            "power_usd_day": power_usd,
            "demand_usd_day": demand_usd,
            "net_marginal_usd_day": net_marginal,
            "net_loaded_usd_day": (net_marginal - (demand_usd or 0)
                                   if net_marginal is not None else None),
            "stale_sources": [],
            "observed_at": None,
            "detail": {"inventory": {"notes": inv.get("notes")}},
        })
    return out


# Emporia readings older than this are not used for allocation
MEASURED_MAX_AGE_S = 15 * 60
# Allocate measured watts across estimated machines only when the estimate is this close
ALLOCATE_TOLERANCE = 0.15


def _apply_costs(row, watts, quality, rate, demand_rate, is_home):
    row["watts"], row["watts_quality"] = watts, quality
    row["power_usd_day"] = watts / 1000 * 24 * rate if watts is not None and rate is not None else None
    row["demand_usd_day"] = (watts / 1000 * demand_rate / DAYS_PER_MONTH
                             if is_home and watts and demand_rate else None)
    exp, pwr = row.get("expected_usd_day"), row["power_usd_day"]
    row["net_marginal_usd_day"] = exp - pwr if exp is not None and pwr is not None else None
    row["net_loaded_usd_day"] = (row["net_marginal_usd_day"] - (row["demand_usd_day"] or 0)
                                 if row["net_marginal_usd_day"] is not None else None)


logger = logging.getLogger(__name__)


def load_overheads() -> dict:
    """Owner-declared support loads on the metered circuits (see settings.FLEET_OVERHEADS)."""
    try:
        with open(settings.FLEET_OVERHEADS, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        logger.warning("Ignoring unreadable %s: %s", settings.FLEET_OVERHEADS, e)
        return {}


def _charge_overhead(rows, overhead_row, serves):
    """Split a support load's cost across the machines it exists for (by their watts),
    so e.g. the immersion tank's pump and dry cooler show up in the Z15's own net."""
    wanted = {norm(n) for n in serves or []}
    served = [r for r in rows if norm(r["name"]) in wanted and r.get("kind") != "overhead"]
    if not served:
        return []
    cost = (overhead_row["power_usd_day"] or 0) + (overhead_row["demand_usd_day"] or 0)
    total_w = sum(r["watts"] or 0 for r in served)
    for r in served:
        share = (r["watts"] or 0) / total_w if total_w else 1 / len(served)
        r["overhead_usd_day"] = cost * share
        r["overhead_label"] = overhead_row["name"]
        r["net_incl_overhead_usd_day"] = (r["net_loaded_usd_day"] - r["overhead_usd_day"]
                                          if r.get("net_loaded_usd_day") is not None else None)
    return [r["name"] for r in served]


def reconcile_home_power(rows, realtime, home_rate, demand_rate, overheads=None):
    """Compare Emporia's measured crypto-circuit watts with the home machines we know.

    - within +/-15%: scale the estimated (nameplate) home machines so the total matches
      the meter; they become "allocated (Emporia crypto circuits)".
    - meter higher by more than 15%: leave machines as estimated and add one
      "Unattributed home mining power" row carrying the gap's cost, so net matches the bill.
    - meter lower: report only (some machines may be on unmonitored circuits).
    Returns the summary block; mutates and may append to rows.
    """
    home = [r for r in rows if (r.get("location") or "").lower() == "home"
            and r.get("kind") != "rental_server"]
    accounted = sum(r["watts"] or 0 for r in home)
    info = {"measured_w": None, "accounted_w": round(accounted), "gap_w": None,
            "measured_at": None, "mode": "no meter",
            # Pool says these aren't hashing, so they're counted at 0 W. If they're still
            # powered on, their draw is inside the gap (and earning nothing).
            # Machines the owner marked inactive in inventory are known to be off.
            "assumed_off": [r["name"] for r in home if r.get("watts_quality") == "assumed off"
                            and r.get("inventory_status") != "inactive"]}
    if not realtime or realtime.get("crypto_mining_w") is None:
        return info
    age = _age_s(realtime.get("measured_at"))
    measured = float(realtime["crypto_mining_w"])
    info.update(measured_w=round(measured), measured_at=realtime.get("measured_at"),
                circuits=realtime.get("circuits") or [])
    if age is None or age > MEASURED_MAX_AGE_S:
        info["mode"] = "meter stale"
        return info
    gap = measured - accounted
    info["gap_w"] = round(gap)

    overhead = (overheads or {}).get("home") or {}
    estimated = [r for r in home if r["watts"] and str(r.get("watts_quality") or "").startswith("estimated")]
    est_sum = sum(r["watts"] for r in estimated)
    fixed = accounted - est_sum
    if overhead.get("label") and gap > 0:
        # A declared support load takes the whole gap. (Allocation is skipped: it would
        # wrongly spread the cooling's watts over the miners.)
        row = {
            "name": overhead["label"], "sources": ["emporia"], "kind": "overhead",
            "location": "home", "model": None, "in_inventory": False, "running": None,
            "status": "measured (meter minus miners)", "algorithm": None, "hashrate_hs": None,
            "device_hashrate_hs": None, "rate_kwh": home_rate, "expected_usd_day": None,
            "expected_basis": ("Emporia crypto circuits minus every known home miner. Includes any "
                               "error in the miners' nameplate watts."),
            "stale_sources": [], "observed_at": realtime.get("measured_at"), "detail": {},
        }
        _apply_costs(row, gap, "measured (Emporia) minus miners", home_rate, demand_rate, True)
        if row["power_usd_day"] is not None:
            row["net_marginal_usd_day"] = -row["power_usd_day"]
            row["net_loaded_usd_day"] = -(row["power_usd_day"] + (row["demand_usd_day"] or 0))
        row["serves"] = _charge_overhead(rows, row, overhead.get("serves"))
        rows.append(row)
        info.update(mode="overhead", overhead_label=overhead["label"], overhead_serves=row["serves"])
    elif measured > 0 and abs(gap) / measured <= ALLOCATE_TOLERANCE and est_sum > 0 and measured > fixed:
        factor = (measured - fixed) / est_sum
        for r in estimated:
            _apply_costs(r, r["watts"] * factor, "allocated (Emporia crypto circuits)",
                         r.get("rate_kwh"), demand_rate, True)
        info["mode"] = "allocated"
        info["gap_w"] = 0
    elif gap > 0:
        row = {
            "name": "Unattributed home mining power", "sources": ["emporia"], "kind": "unattributed",
            "location": "home", "model": None, "in_inventory": False, "running": None,
            "status": "measured, no machine", "algorithm": None, "hashrate_hs": None,
            "device_hashrate_hs": None, "rate_kwh": home_rate, "expected_usd_day": None,
            "expected_basis": ("Emporia crypto circuits minus every known home machine. Usually a "
                               "machine that is powered but not hashing, or one not in inventory."),
            "stale_sources": [], "observed_at": realtime.get("measured_at"), "detail": {},
        }
        _apply_costs(row, gap, "measured (Emporia) minus known", home_rate, demand_rate, True)
        # Its net is just its cost: nothing earns from it
        if row["power_usd_day"] is not None:
            row["net_marginal_usd_day"] = -row["power_usd_day"]
            row["net_loaded_usd_day"] = -(row["power_usd_day"] + (row["demand_usd_day"] or 0))
        rows.append(row)
        info["mode"] = "unattributed gap"
    else:
        info["mode"] = "meter below estimates"
    return info


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
    kryptex_rates = (accounts.get("kryptex:rates") or {}).get("data", {}).get("rates", {})
    inventory = _inventory_index(inventory_miners, inventory_locations)
    matched_inventory_ids = set()

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
        inv = _match_inventory(inventory, primary["name"])
        if inv:
            matched_inventory_ids.add(inv.get("id"))

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
        kryptex_coin = None
        if pool:
            algorithm = pool.get("algorithm")
            if pool["source"] == "kryptex":
                kryptex_coin = algorithm
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
        kx_rate = kryptex_rates.get(kryptex_coin or "")
        if device and device["source"] == "clore":
            expected_basis = "rental price shown in detail (renter price, before Clore fees)"
        elif hashrate and kx_rate:
            # The coin actually mined, at Kryptex's network hashrate, emission, fee and price
            expected_usd = hashrate * kx_rate["usd_per_hs_day"]
            expected_basis = (f"Kryptex {kryptex_coin.upper()}: hashrate / network x daily emission"
                              f" x (1 - fee) x ${kx_rate['price_usd']:.4g}")
        elif hashrate and rate_info and btc_usd:
            expected_usd = hashrate * rate_info["btc_per_hs_day"] * btc_usd
            expected_basis = f"PowerPool pay rate ({rate_info['units']}) x pool hashrate"

        power_usd = watts / 1000 * 24 * rate if watts is not None and rate is not None else None
        demand_usd = (watts / 1000 * demand_rate / DAYS_PER_MONTH
                      if is_home and watts and demand_rate else None)
        net_marginal = (expected_usd - power_usd
                        if expected_usd is not None and power_usd is not None else None)
        net_loaded = (net_marginal - (demand_usd or 0) if net_marginal is not None else None)

        if running is False and (inv or {}).get("status") == "inactive":
            status = "off (marked inactive)"

        rows.append({
            "name": primary["name"],
            "sources": sorted(by_source),
            "kind": (device or pool).get("kind") or "asic",
            "location": location,
            "model": ((device or {}).get("extra") or {}).get("model") or (inv or {}).get("model"),
            "in_inventory": inv is not None,
            "inventory_status": (inv or {}).get("status"),
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

    rows.extend(_inventory_only_rows(inventory_miners, inventory_locations, matched_inventory_ids,
                                     rates, btc_usd, home_rate, demand_rate))
    realtime = (accounts.get("electricity:realtime") or {}).get("data")
    home_power = reconcile_home_power(rows, realtime, home_rate, demand_rate, load_overheads())
    support = ("unattributed", "overhead")
    rows.sort(key=lambda r: (r["location"] or "zzz", r["kind"] in support, r["name"].lower()))
    machine_rows = [r for r in rows if r["kind"] not in support]

    def total(field):
        vals = [r[field] for r in rows if r[field] is not None]
        return round(sum(vals), 2) if vals else None

    summary = {
        "machines": len(machine_rows),
        "running": sum(1 for r in machine_rows if r["running"] is True),
        "not_running": sum(1 for r in machine_rows if r["running"] is False),
        "unknown": sum(1 for r in machine_rows if r["running"] is None),
        "expected_usd_day": total("expected_usd_day"),
        "power_usd_day": total("power_usd_day"),
        "demand_usd_day": total("demand_usd_day"),
        "net_marginal_usd_day": total("net_marginal_usd_day"),
        "net_loaded_usd_day": total("net_loaded_usd_day"),
        "rows_no_live_source": sum(1 for r in rows if r["sources"] == ["inventory"]),
        "rows_missing_cost": sum(1 for r in machine_rows
                                 if r["power_usd_day"] is None and r["kind"] != "rental_server"),
        "home_power": home_power,
        "btc_usd": btc_usd,
        "home_rate_kwh": home_rate,
        "demand_rate_kw_month": demand_rate,
    }

    account_view = []
    for a in data["accounts"]:
        if a["account_key"] in ("powerpool:payrates", "electricity:rates", "electricity:realtime",
                                "kryptex:rates"):
            continue
        d = a["data"]
        item = {"account": a["account_key"], "source": a["source"], "fetched_at": a["fetched_at"], "data": d}
        if a["source"] == "kryptex":
            coin = (d.get("coin") or "").upper()
            last7 = sorted(d.get("daily_rewards") or [], key=lambda p: p.get("date") or 0)[-7:]
            reward7 = sum(p.get("reward") or 0 for p in last7)
            item["actual_last_7d_coin"] = reward7
            price = prices.get(coin) or (kryptex_rates.get(coin.lower()) or {}).get("price_usd")
            item["actual_last_7d_usd"] = reward7 * price if price else None
        account_view.append(item)

    return {
        "generated_at": store.utcnow(),
        "summary": summary,
        "machines": rows,
        "accounts": account_view,
        "sources": source_health(data["sources"]),
    }
