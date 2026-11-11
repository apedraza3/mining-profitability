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
DEVICE_PRIORITY = ("lan", "foreman", "clore", "vast")
# GPU rental marketplaces: one row per listed server, keyed by source (not merged by name)
RENTAL_SOURCES = ("clore", "vast")
# Clore host fees (no PoH / MFP discounts): base on every order, extra on BTC/USD orders
CLORE_BASE_FEE = 0.05
CLORE_EXTRA_FEE = 0.15
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


# PDU readings older than this are not used as machine watts
PDU_MAX_AGE_S = 5 * 60


def pdu_circuit_readings(pdu_accounts) -> dict:
    """{"<pdu>:<branch>": {"watts", "unit", "fresh"}} from the pdu:* accounts.

    "<pdu>:total" is the whole PDU. Branches without a watts column (Chatsworth
    CPI-UNITY, APC banks) are VA; server PSUs run at PF ~0.99, so VA is used as W
    and the quality label says so.
    """
    out = {}
    for a in pdu_accounts:
        d = a.get("data") or {}
        name = d.get("name")
        if not name or d.get("error"):
            continue
        age = _age_s(a.get("fetched_at"))
        fresh = age is not None and age <= PDU_MAX_AGE_S
        total_unit = "VA" if "VA" in (d.get("power_kind") or "") else "W"
        if d.get("total_w") is not None:
            out[f"{name}:total"] = {"watts": float(d["total_w"]), "unit": total_unit, "fresh": fresh}
        for b in d.get("branches") or []:
            if b.get("watts") is not None:
                out[f"{name}:{b['branch']}"] = {"watts": float(b["watts"]), "unit": "W", "fresh": fresh}
            elif b.get("va") is not None:
                out[f"{name}:{b['branch']}"] = {"watts": float(b["va"]), "unit": "VA", "fresh": fresh}
    return out


def _circuit_key(raw: str) -> str:
    return norm(raw.split(":", 1)[0]) + ":" + norm(raw.split(":", 1)[1]) if ":" in (raw or "") else norm(raw)


def _find_circuit(readings: dict, wanted: str):
    target = _circuit_key(wanted)
    for key, value in readings.items():
        if _circuit_key(key) == target:
            return key, value
    return None, None


def apply_pdu_circuits(rows, readings, circuits, home_rate, demand_rate) -> list[dict]:
    """Replace nameplate watts with PDU-measured watts for machines on a mapped circuit.

    ``circuits`` comes from fleet_overheads.json: {"apc:Bank 1": ["Z11-1", "Z11-2"]}.
    A circuit's reading is split across its running machines by their current watts
    (nameplate), or equally when none are known. Machines reported off keep 0 W; if
    the circuit still draws power, that's listed as unassigned on the circuit.
    """
    info = []
    for circuit, names in (circuits or {}).items():
        if not isinstance(names, list):
            continue
        key, reading = _find_circuit(readings, circuit)
        entry = {"circuit": circuit, "machines": names, "watts": None, "unit": None,
                 "applied_to": [], "unassigned_w": 0, "state": "no reading"}
        info.append(entry)
        if not reading:
            continue
        entry.update(watts=round(reading["watts"]), unit=reading["unit"])
        if not reading["fresh"]:
            entry["state"] = "stale"
            continue
        wanted = {norm(n) for n in names}
        members = [r for r in rows if norm(r["name"]) in wanted
                   and r.get("kind") not in ("overhead", "unattributed", "non_mining")]
        drawing = [r for r in members if r.get("running") is not False]
        if not drawing:
            entry["state"] = "no running machine"
            entry["unassigned_w"] = round(reading["watts"])
            continue
        basis = sum(r["watts"] or 0 for r in drawing)
        for r in drawing:
            share = (r["watts"] or 0) / basis if basis else 1 / len(drawing)
            quality = f"measured (PDU {key}" + (", VA" if reading["unit"] == "VA" else "") + \
                      (", split by nameplate" if len(drawing) > 1 else "") + ")"
            rate = r.get("rate_kwh")
            if rate is None and (r.get("location") or "").lower() == "home":
                rate = home_rate
            _apply_costs(r, reading["watts"] * share, quality, rate, demand_rate,
                         (r.get("location") or "").lower() == "home")
            entry["applied_to"].append(r["name"])
        entry["state"] = "applied"
    return info


def reconcile_home_power(rows, realtime, home_rate, demand_rate, overheads=None, readings=None):
    """Compare Emporia's measured crypto-circuit watts with the home machines we know.

    - within +/-15%: scale the estimated (nameplate) home machines so the total matches
      the meter; they become "allocated (Emporia crypto circuits)".
    - meter higher by more than 15%: leave machines as estimated and add one
      "Unattributed home mining power" row carrying the gap's cost, so net matches the bill.
    - meter lower: report only (some machines may be on unmonitored circuits).
    Returns the summary block; mutates and may append to rows.
    """
    # Every home machine on the metered sub-panel counts, rental servers included (the Clore
    # G481 is on it). Machines listed in fleet_overheads.json "off_meter" are on other circuits.
    off_meter = {norm(n) for n in (overheads or {}).get("off_meter") or []}
    home = [r for r in rows if (r.get("location") or "").lower() == "home"
            and r.get("kind") not in ("overhead", "unattributed", "non_mining")
            and norm(r["name"]) not in off_meter]
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

    declared = (overheads or {}).get("home") or []
    loads = [declared] if isinstance(declared, dict) else [l for l in declared if isinstance(l, dict)]
    loads = [l for l in loads if l.get("label")]
    # A load with "pdu": "chatsworth:CB1" uses that circuit's reading instead of taking
    # a share of the gap. Measured loads are subtracted first, so the gap left for the
    # unmeasured ones (and for "unexplained") is as small as possible.
    for load in loads:
        load.pop("_measured", None)
        if load.get("pdu") and readings:
            key, reading = _find_circuit(readings, load["pdu"])
            if reading and reading["fresh"]:
                load["_measured"] = (key, reading)
    loads.sort(key=lambda l: "_measured" not in l)
    estimated = [r for r in home if r["watts"] and str(r.get("watts_quality") or "").startswith("estimated")]
    est_sum = sum(r["watts"] for r in estimated)
    fixed = accounted - est_sum
    def support_row(name, kind, status, basis, watts, quality):
        row = {
            "name": name, "sources": ["emporia"], "kind": kind, "location": "home",
            "model": None, "in_inventory": False, "running": None, "status": status,
            "algorithm": None, "hashrate_hs": None, "device_hashrate_hs": None,
            "rate_kwh": home_rate, "expected_usd_day": None, "expected_basis": basis,
            "stale_sources": [], "observed_at": realtime.get("measured_at"), "detail": {},
        }
        _apply_costs(row, watts, quality, home_rate, demand_rate, True)
        # Its net is just its cost: nothing earns from it
        if row["power_usd_day"] is not None:
            row["net_marginal_usd_day"] = -row["power_usd_day"]
            row["net_loaded_usd_day"] = -(row["power_usd_day"] + (row["demand_usd_day"] or 0))
        rows.append(row)
        return row

    unattributed_basis = ("Emporia crypto circuits minus every known home machine. Usually a "
                          "machine that is powered but not hashing, one not in inventory, or "
                          "miners drawing more than their nameplate watts.")
    if loads and (gap > 0 or any("_measured" in l for l in loads)):
        # Declared loads take the gap in order, each up to its rated maximum (max_w; no
        # max = whatever is left). Mining support loads (cooling) are charged to the
        # machines they serve; "mining": false loads (server, workshop) are shown but kept
        # out of the mining totals. Anything left over stays unattributed. Allocation
        # across nameplate estimates is skipped: it would spread these watts over miners.
        remaining = gap
        info.update(mode="overhead", loads=[])
        for load in loads:
            measured_load = load.get("_measured")
            if remaining <= 0 and not measured_load:
                break
            max_w = load.get("max_w")
            is_mining = load.get("mining", True) is not False
            if measured_load:
                key, reading = measured_load
                w = reading["watts"]
                quality = f"measured (PDU {key}" + (", VA" if reading["unit"] == "VA" else "") + ")"
                status = "measured (PDU)" if is_mining else "not mining"
                basis = (f"Measured on PDU circuit {key}."
                         + (" " + load["note"] if load.get("note") else "")
                         + ("" if is_mining else " Not mining: excluded from the mining totals."))
            else:
                w = min(remaining, float(max_w)) if max_w else remaining
                quality = ("measured (Emporia) minus miners"
                           + (", capped at rated max" if max_w and w >= float(max_w) else ""))
                status = "measured (meter minus miners)" if is_mining else "not mining"
                basis = ("Emporia crypto circuits minus every known home miner"
                         + (" and the loads above it" if info["loads"] else "") + "."
                         + (f" Capped at its rated max of {float(max_w):.0f} W." if max_w else
                            " Takes whatever is left, so it also absorbs any error in the miners' nameplate watts.")
                         + (" " + load["note"] if load.get("note") else "")
                         + ("" if is_mining else " Not mining: excluded from the mining totals."))
            remaining -= w
            row = support_row(load["label"], "overhead" if is_mining else "non_mining",
                              status, basis, w, quality)
            if is_mining:
                row["serves"] = _charge_overhead(rows, row, load.get("serves"))
                info.setdefault("overhead_label", load["label"])
                info.setdefault("overhead_w", round(w))
            else:
                row["net_marginal_usd_day"] = row["net_loaded_usd_day"] = None
                info["non_mining_w"] = info.get("non_mining_w", 0) + round(w)
            info["loads"].append({"label": load["label"], "watts": round(w), "mining": is_mining,
                                  "measured": bool(measured_load)})
            load.pop("_measured", None)
        info["unattributed_w"] = round(max(remaining, 0))
        if remaining > 50:
            support_row("Unattributed home mining power", "unattributed", "measured, no machine",
                        unattributed_basis + " This is what's left after the declared loads.",
                        remaining, "measured (Emporia) minus known")
    elif measured > 0 and abs(gap) / measured <= ALLOCATE_TOLERANCE and est_sum > 0 and measured > fixed:
        factor = (measured - fixed) / est_sum
        for r in estimated:
            _apply_costs(r, r["watts"] * factor, "allocated (Emporia crypto circuits)",
                         r.get("rate_kwh"), demand_rate, True)
        info["mode"] = "allocated"
        info["gap_w"] = 0
    elif gap > 0:
        support_row("Unattributed home mining power", "unattributed", "measured, no machine",
                    unattributed_basis, gap, "measured (Emporia) minus known")
        info["mode"] = "unattributed gap"
    else:
        info["mode"] = "meter below estimates"
    return info


def solar_summary(realtime, home_power, home_rate) -> dict | None:
    """Solar as the electricity dashboard sees it, and mining's share of its value.

    CORE nets kWh monthly at the retail rate (Sep bill: 5,928 used - 25 exported = 5,903
    billed), so solar doesn't change what the next mining kWh costs: every kWh still
    displaces a retail kWh. Fleet costs therefore stay at the marginal rate, and solar is
    shown as a separate credit: mining's share of house use x the solar it offset over the
    last 24 h x the marginal rate. Same proportional method as the Solar page.
    """
    if not realtime:
        return None
    out = {"solar_w": realtime.get("solar_w"), "house_w": realtime.get("house_w"),
           "net_grid_w": realtime.get("net_grid_w")}
    day = realtime.get("last24h") or {}
    if not day.get("house_kwh"):
        return out
    meter_w = home_power.get("measured_w") or 0
    non_mining_w = home_power.get("non_mining_w") or 0
    mining_fraction = (meter_w - non_mining_w) / meter_w if meter_w else 1.0
    mining_kwh = (day.get("crypto_kwh") or 0) * mining_fraction
    share = min(mining_kwh / day["house_kwh"], 1.0)
    offset_kwh = min(day.get("solar_kwh") or 0, day["house_kwh"])
    out.update(
        hours=day.get("hours"), solar_kwh_24h=day.get("solar_kwh"), house_kwh_24h=day["house_kwh"],
        mining_kwh_24h=round(mining_kwh, 1), mining_share=round(share, 3),
        mining_solar_credit_usd_day=(round(offset_kwh * share * home_rate, 2) if home_rate else None),
    )
    return out


# Warn when the projected clock-hour average comes within this of the month's peak
DEMAND_MARGIN_KW = 0.5


def demand_status(peak_kw, now_w, hour_points, now=None, margin_kw=DEMAND_MARGIN_KW):
    """CORE bills the month's highest clock-hour average grid import. Project the current
    hour from its 5-minute snapshots so far plus the live reading (assumed to hold for the
    rest of the hour) and compare with the month's peak so far."""
    now = now or datetime.now(timezone.utc)
    minutes = now.minute + now.second / 60
    seen = [p["net_grid_w"] for p in hour_points if isinstance(p.get("net_grid_w"), (int, float))]
    if now_w is None and not seen:
        return None
    so_far_w = sum(seen) / len(seen) if seen else now_w
    live_w = now_w if now_w is not None else so_far_w
    projected_w = (so_far_w * minutes + live_w * (60 - minutes)) / 60
    out = {"hour_avg_kw": round(so_far_w / 1000, 2), "live_kw": round(live_w / 1000, 2),
           "projected_kw": round(projected_w / 1000, 2), "minutes_into_hour": round(minutes),
           "peak_kw": peak_kw}
    if peak_kw:
        headroom = peak_kw - projected_w / 1000
        out["headroom_kw"] = round(headroom, 2)
        out["status"] = "over" if headroom < 0 else "close" if headroom < margin_kw else "ok"
    return out


def _demand_block(elec, realtime, demand_rate):
    hour_start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0).isoformat()
    status = demand_status(elec.get("house_peak_kw"), (realtime or {}).get("net_grid_w"),
                           store.read_snapshots(hour_start))
    if status is None:
        return None
    status["demand_rate_kw_month"] = demand_rate
    status["peak_basis"] = elec.get("peak_basis")
    status["bill"] = elec.get("bill")
    return status


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
    vast_earnings = (accounts.get("vast:earnings") or {}).get("data", {})
    inventory =_inventory_index(inventory_miners, inventory_locations)
    matched_inventory_ids = set()

    # Group rows from different sources that describe the same machine (by name)
    groups: dict[str, list[dict]] = {}
    for m in data["machines"]:
        key = (f"{m['source']}:{m['name']}" if m["source"] in RENTAL_SOURCES else norm(m["name"]))
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
        if device and device["source"] in RENTAL_SOURCES:
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
        if device and device["source"] in RENTAL_SOURCES + ("foreman",):
            # Clore/Vast don't know where the box sits, and Foreman's fixed label may not
            # match the inventory's location name; the inventory decides (and sets the rate)
            location = ((inv or {}).get("_location", {}).get("name") or device.get("location")
                        or device["source"])
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
            prices_by_ccy = (device.get("extra") or {}).get("rented_price_by_currency") or {}
            usd_price = next((v for k, v in prices_by_ccy.items() if k.upper().startswith("USD")), None)
            btc_price = next((v for k, v in prices_by_ccy.items() if "bitcoin" in k.lower() or k.upper() == "BTC"), None)
            # Host keeps 1 - base fee - extra fee. Extra applies to BTC/USD-stablecoin orders,
            # not CLORE ones. Owner's 2026-11-09/10 statement: $22 gross -> $1.10 base +
            # $3.30 extra -> $17.60 revenue, i.e. 5% + 15%.
            keep = 1 - CLORE_BASE_FEE - CLORE_EXTRA_FEE
            if usd_price is not None:
                # "USD-Blockchain" = stablecoin-settled order, priced in dollars per day
                expected_usd = float(usd_price) * keep
                expected_basis = (f"Clore renter price ${float(usd_price):.2f}/day minus host fees "
                                  f"({CLORE_BASE_FEE:.0%} base + {CLORE_EXTRA_FEE:.0%} extra on USD orders)")
            elif btc_price is not None and btc_usd:
                expected_usd = float(btc_price) * btc_usd * keep
                expected_basis = (f"Clore renter price in BTC x BTC price minus host fees "
                                  f"({CLORE_BASE_FEE:.0%} base + {CLORE_EXTRA_FEE:.0%} extra on BTC orders)")
            elif prices_by_ccy:
                expected_basis = "rented, priced in " + ", ".join(prices_by_ccy) + " (no USD price for that currency)"
            else:
                expected_basis = "not rented right now"
        elif device and device["source"] == "vast":
            vast_id = str((device.get("extra") or {}).get("id"))
            earned_7d = (vast_earnings.get("per_machine_7d_usd") or {}).get(vast_id)
            if earned_7d is not None:
                expected_usd = earned_7d / 7
                expected_basis = "Vast earnings, average of the last 7 days (actual, after Vast's fee)"
            else:
                expected_basis = "no Vast earnings breakdown for this machine yet"
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
    overheads = load_overheads()
    pdu_accounts = [a for a in data["accounts"] if a["account_key"].startswith("pdu:")]
    readings = pdu_circuit_readings(pdu_accounts)
    pdu_circuits = apply_pdu_circuits(rows, readings, overheads.get("pdu_circuits"),
                                      home_rate, demand_rate)
    home_power = reconcile_home_power(rows, realtime, home_rate, demand_rate, overheads, readings)
    support = ("unattributed", "overhead", "non_mining")
    rows.sort(key=lambda r: (r["location"] or "zzz", r["kind"] in support, r["name"].lower()))
    machine_rows = [r for r in rows if r["kind"] not in support]

    def total(field):
        # Non-mining loads (server, workshop) share the meter but not the mining P&L
        vals = [r[field] for r in rows if r[field] is not None and r["kind"] != "non_mining"]
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
        "solar": solar_summary(realtime, home_power, home_rate),
        "pdus": [{k: a["data"].get(k) for k in ("name", "host", "total_w", "power_kind", "peak_w",
                                                  "energy_kwh", "branches", "error", "sys_name")}
                 | {"fetched_at": a["fetched_at"]}
                 for a in pdu_accounts],
        "pdu_circuits": pdu_circuits,
        "fan_control": (accounts.get("fancontrol:basement") or {}).get("data"),
        "alerts": (accounts.get("alerts:active") or {}).get("data"),
        "demand": _demand_block(elec, realtime, demand_rate),
        "climate": ((accounts.get("climate:basement") or {}).get("data") or None) and
                   {**accounts["climate:basement"]["data"], "fetched_at": accounts["climate:basement"]["fetched_at"]},
        "btc_usd": btc_usd,
        "home_rate_kwh": home_rate,
        "demand_rate_kw_month": demand_rate,
    }

    account_view = []
    for a in data["accounts"]:
        if (a["account_key"] in ("powerpool:payrates", "electricity:rates", "electricity:realtime",
                                 "kryptex:rates", "climate:basement", "fancontrol:basement",
                                 "alerts:active")
                or a["account_key"].startswith(("gputemps:", "pdu:"))):
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
