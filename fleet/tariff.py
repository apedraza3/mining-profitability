"""CORE Electric rate plans and what this billing period costs on each.

Source: CORE Rates & Regulations (rate schedules effective 1/1/2026, BR25-46) and the
January 2027 summary of rate changes / redline (BR26-39). See
reports/CORE Electric rate plan options.md (2026-11-13). Update this table when CORE
publishes new rates; nothing else needs to change.

CORE bills demand on the highest 60-minute average grid import (net of rooftop solar).
SG1 uses 15 minutes. We only have clock-hour averages, which are a lower bound of a
rolling window, so every demand figure here is a lower bound.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Denver")
ONPEAK_HOURS = range(16, 20)  # 4-8 PM local, every day

# code -> [(effective date, rates)], oldest first.
#   base: $/month; energy: $/kWh (or energy_on/energy_off for AT)
#   d_any: $/kW on the period's highest hour at any time
#   d_on:  $/kW on the period's highest hour within 4-8 PM
SCHEDULES = {
    "C": {"label": "C - Residential demand", "rates": [
        ("2026-01-01", {"base": 20.00, "energy": 0.08907, "d_any": 15.38}),
        ("2027-01-01", {"base": 21.55, "energy": 0.09597, "d_any": 16.57})]},
    "CT": {"label": "CT - C with the 4-8 PM demand rider", "rates": [
        ("2026-01-01", {"base": 20.00, "energy": 0.08907, "d_any": 7.85, "d_on": 7.53}),
        ("2027-01-01", {"base": 21.55, "energy": 0.09597, "d_any": 8.46, "d_on": 8.11})]},
    "A": {"label": "A - Residential standard", "rates": [
        ("2026-01-01", {"base": 20.00, "energy": 0.11550, "d_on": 5.47}),
        ("2027-01-01", {"base": 21.55, "energy": 0.12445, "d_on": 5.89})]},
    "AT": {"label": "AT - Residential time of use", "rates": [
        ("2026-01-01", {"base": 20.00, "energy_on": 0.27050, "energy_off": 0.08558, "d_any": 5.47}),
        ("2027-01-01", {"base": 21.55, "energy_on": 0.29146, "energy_off": 0.09221, "d_any": 5.89})]},
    "SG1": {"label": "SG1 - Small general service (business)", "rates": [
        ("2026-01-01", {"base": 32.00, "energy": 0.07594, "d_any": 12.00}),
        ("2027-01-01", {"base": 34.48, "energy": 0.08183, "d_any": 12.93})],
        "note": "15-minute demand: real peak is higher than the hourly figure used here"},
}


def rates_for(code: str, when: datetime) -> tuple[str, dict]:
    day = when.astimezone(TZ).date().isoformat()
    effective, rates = SCHEDULES[code]["rates"][0]
    for eff, r in SCHEDULES[code]["rates"]:
        if eff <= day:
            effective, rates = eff, r
    return effective, rates


def is_onpeak(when: datetime) -> bool:
    return when.astimezone(TZ).hour in ONPEAK_HOURS


def parse_hours(rows) -> list[tuple[datetime, float]]:
    """[(hour start UTC, net import kWh)] from electricity-dashboard hourly rows
    ("YYYY-MM-DD HH:00:00" naive UTC, net_grid_wh). Rows without data are skipped."""
    out = []
    for r in rows or []:
        hs, wh = (r[0], r[1]) if isinstance(r, (list, tuple)) else (r.get("hour_start"), r.get("net_grid_wh"))
        if hs is None or not isinstance(wh, (int, float)):
            continue
        t = datetime.fromisoformat(str(hs).replace(" ", "T"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        out.append((t, wh / 1000))
    return sorted(out)


def peaks(hours) -> dict:
    """Highest clock-hour average import at any time and within 4-8 PM local."""
    def top(items):
        if not items:
            return None
        t, kwh = max(items, key=lambda x: x[1])
        return {"kw": round(kwh, 2), "at": t.isoformat()}
    return {"any": top(hours), "onpeak": top([h for h in hours if is_onpeak(h[0])])}


def plan_bill(code, hours, period_start, hours_in_period, wpca, tax, peak_floor_kw=None) -> dict:
    """Projected bill for the whole period on one plan. Energy is scaled up from the hours
    measured; peaks are the ones set so far (they can only grow). peak_floor_kw raises the
    any-hour peak to a value seen by another meter (e.g. before our history starts)."""
    effective, r = rates_for(code, period_start)
    scale = hours_in_period / len(hours) if hours else 0
    on_kwh = max(sum(k for t, k in hours if is_onpeak(t)), 0) * scale
    all_kwh = max(sum(k for _, k in hours), 0) * scale
    off_kwh = max(all_kwh - on_kwh, 0)
    pk = peaks(hours)
    p_any = max((pk["any"] or {}).get("kw") or 0, peak_floor_kw or 0)
    p_on = (pk["onpeak"] or {}).get("kw") or 0
    if "energy_on" in r:
        energy = on_kwh * r["energy_on"] + off_kwh * r["energy_off"]
    else:
        energy = all_kwh * r["energy"]
    demand = p_any * r.get("d_any", 0) + p_on * r.get("d_on", 0)
    wpca_usd = all_kwh * wpca
    total = (r["base"] + energy + demand + wpca_usd) * (1 + tax)
    return {"code": code, "label": SCHEDULES[code]["label"], "rates_effective": effective,
            "month_usd": round(total, 2), "energy_usd": round(energy * (1 + tax), 2),
            "demand_usd": round(demand * (1 + tax), 2), "kwh": round(all_kwh),
            "note": SCHEDULES[code].get("note")}


def marginal_kwh(code, when, wpca, tax) -> float:
    """What one more kWh costs right now on a plan (energy only, before any new peak)."""
    _, r = rates_for(code, when)
    e = r.get("energy") if "energy" in r else (r["energy_on"] if is_onpeak(when) else r["energy_off"])
    return round((e + wpca) * (1 + tax), 5)


def billed_peaks_now(code, when, pk) -> list[dict]:
    """The peaks a new high this hour would raise on this plan, with $/kW-month (pre-tax)."""
    _, r = rates_for(code, when)
    out = []
    if r.get("d_any"):
        out.append({"which": "any", "peak_kw": (pk.get("any") or {}).get("kw"), "rate": r["d_any"]})
    if r.get("d_on") and is_onpeak(when):
        out.append({"which": "onpeak", "peak_kw": (pk.get("onpeak") or {}).get("kw"), "rate": r["d_on"]})
    return out


def summarize(current, hours_rows, period_start_iso, hours_in_period, wpca, tax,
              peak_floor_kw=None, now=None) -> dict | None:
    now = now or datetime.now(timezone.utc)
    if current not in SCHEDULES:
        current = "C"
    hours = parse_hours(hours_rows)
    if period_start_iso:
        start = datetime.fromisoformat(period_start_iso)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        hours = [h for h in hours if h[0] >= start]
    else:
        local = now.astimezone(TZ)
        start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    if not hours:
        return None
    hours_in_period = hours_in_period or 24 * 30
    plans = [plan_bill(c, hours, start, hours_in_period, wpca, tax, peak_floor_kw) for c in SCHEDULES]
    cur = next(p for p in plans if p["code"] == current)
    best = min(plans, key=lambda p: p["month_usd"])
    pk = peaks(hours)
    if peak_floor_kw and (pk["any"] is None or peak_floor_kw > pk["any"]["kw"]):
        pk["any"] = {"kw": round(peak_floor_kw, 2), "at": None}
    nxt_eff, nxt_rates = rates_for(current, now + timedelta(days=62))
    return {
        "schedule": current, "label": SCHEDULES[current]["label"],
        "period_start": start.isoformat(), "hours_measured": len(hours),
        "hours_in_period": hours_in_period, "peaks": pk,
        "plans": sorted(plans, key=lambda p: p["month_usd"]),
        "current_month_usd": cur["month_usd"], "cheapest": best["code"],
        "save_month_usd": round(cur["month_usd"] - best["month_usd"], 2),
        "onpeak_now": is_onpeak(now),
        "marginal_kwh_now": marginal_kwh(current, now, wpca, tax),
        "billed_peaks_now": billed_peaks_now(current, now, pk),
        "next_rates": ({"effective": nxt_eff, **nxt_rates}
                       if nxt_eff > rates_for(current, now)[0] else None),
        "tax": tax, "wpca": wpca,
        "basis": "clock-hour net import (lower bound of CORE's 60-minute demand)",
    }
