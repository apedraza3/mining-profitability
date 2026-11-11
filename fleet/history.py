"""Fleet history: a compact snapshot of the Fleet Now totals every few minutes.

The poller writes one snapshot per SNAPSHOT_EVERY_S (it already holds every reading the
view needs, and reads the inventory from data/). The web app only reads them back,
averaged into buckets so a 30-day chart stays a few hundred points.
"""

from datetime import datetime, timedelta, timezone

from fleet import settings, store

SNAPSHOT_EVERY_S = 300
MAX_POINTS = 400


def _r(v, nd=2):
    return round(v, nd) if isinstance(v, (int, float)) else None


def compact(fleet: dict) -> dict:
    """The few numbers worth charting, from a build_fleet() result."""
    sm = fleet.get("summary") or {}
    hp = sm.get("home_power") or {}
    solar = sm.get("solar") or {}
    machines = [r for r in fleet.get("machines") or []
                if r.get("kind") not in ("overhead", "unattributed", "non_mining")]
    hashrate = {}
    for r in machines:
        if r.get("running") and r.get("hashrate_hs") and r.get("algorithm"):
            hashrate[r["algorithm"]] = hashrate.get(r["algorithm"], 0) + r["hashrate_hs"]
    return {
        "running": sm.get("running"),
        "machines": sm.get("machines"),
        "expected_usd_day": _r(sm.get("expected_usd_day")),
        "power_usd_day": _r(sm.get("power_usd_day")),
        "demand_usd_day": _r(sm.get("demand_usd_day")),
        "net_loaded_usd_day": _r(sm.get("net_loaded_usd_day")),
        "solar_credit_usd_day": _r(solar.get("mining_solar_credit_usd_day")),
        "meter_w": _r(hp.get("measured_w"), 0),
        "miners_w": _r(hp.get("accounted_w"), 0),
        "non_mining_w": _r(hp.get("non_mining_w"), 0),
        "solar_w": _r(solar.get("solar_w"), 0),
        "house_w": _r(solar.get("house_w"), 0),
        "pdus_w": {p["name"]: _r(p.get("total_w"), 0) for p in sm.get("pdus") or []
                   if p.get("name") and not p.get("error")},
        "hashrate_hs": hashrate,
        "btc_usd": _r(sm.get("btc_usd"), 0),
        "basement_temp_f": _r((sm.get("climate") or {}).get("temp_f"), 1),
        "basement_humidity_pct": _r((sm.get("climate") or {}).get("humidity_pct"), 1),
        "dew_point_f": _r((sm.get("climate") or {}).get("dew_point_f"), 1),
        "fan_speed": {f["label"]: f.get("speed") for f in (sm.get("climate") or {}).get("fans") or []
                      if isinstance(f.get("speed"), (int, float))},
    }


def _build_with_inventory() -> dict:
    """build_fleet() the way the web app does it, reading the inventory from data/."""
    from fleet.view import build_fleet
    from services.inventory_manager import InventoryManager

    data_dir = settings.BASE_DIR / "data"
    inv = InventoryManager(str(data_dir / "inventory.json"), str(data_dir / "locations.json"))
    return build_fleet(inv.get_all_miners(), inv.get_all_locations())


def take_snapshot():
    """Store the compact totals of the current fleet view."""
    store.record_snapshot(compact(_build_with_inventory()))


def _bucket_avg(points: list[dict], bucket_s: int) -> list[dict]:
    """Average numeric fields (and one level of nested dicts) per time bucket."""
    out, cur, cur_key = [], [], None

    def flush():
        if not cur:
            return
        merged = {"ts": cur[0]["ts"]}
        for key in cur[0]:
            if key == "ts":
                continue
            vals = [p.get(key) for p in cur]
            if all(isinstance(v, dict) for v in vals if v is not None) and any(vals):
                sub = {}
                for d in vals:
                    for k, v in (d or {}).items():
                        if isinstance(v, (int, float)):
                            sub.setdefault(k, []).append(v)
                merged[key] = {k: round(sum(v) / len(v), 2) for k, v in sub.items()}
            else:
                nums = [v for v in vals if isinstance(v, (int, float))]
                merged[key] = round(sum(nums) / len(nums), 2) if nums else None
        out.append(merged)

    for p in points:
        ts = datetime.fromisoformat(p["ts"])
        key = int(ts.timestamp()) // bucket_s
        if key != cur_key:
            flush()
            cur, cur_key = [], key
        cur.append(p)
    flush()
    return out


def _hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)


def summarize_hour(points: list[dict], samples: list[dict]) -> dict:
    """Averages of every snapshot field, plus peak/min meter power, kWh and per-machine means."""
    avg = _bucket_avg(points, 3600)[0] if points else {}
    avg.pop("ts", None)
    meter = [p["meter_w"] for p in points if isinstance(p.get("meter_w"), (int, float))]
    out = {**avg, "points": len(points)}
    if meter:
        out.update(meter_w_max=max(meter), meter_w_min=min(meter),
                   meter_kwh=round(sum(meter) / len(meter) / 1000, 3))
    per = {}
    for s in samples:
        m = per.setdefault(s["machine_key"], {"hs": [], "w": [], "run": []})
        if isinstance(s.get("hashrate_hs"), (int, float)):
            m["hs"].append(s["hashrate_hs"])
        if isinstance(s.get("watts"), (int, float)):
            m["w"].append(s["watts"])
        if s.get("running") is not None:
            m["run"].append(1 if s["running"] else 0)
    out["machines"] = {k: {"hashrate_hs": round(sum(v["hs"]) / len(v["hs"]), 2) if v["hs"] else None,
                           "watts": round(sum(v["w"]) / len(v["w"]), 1) if v["w"] else None,
                           "uptime": round(sum(v["run"]) / len(v["run"]), 3) if v["run"] else None}
                       for k, v in per.items()}
    return out


def rollup(now: datetime | None = None, max_hours: int = 24 * 120) -> int:
    """Write every completed hour not yet rolled up (backfills from the oldest snapshot)."""
    now = now or datetime.now(timezone.utc)
    last = store.last_hourly()
    if last:
        start = datetime.fromisoformat(last) + timedelta(hours=1)
    else:
        first = store.read_snapshots("0000")[:1]
        if not first:
            return 0
        start = _hour(datetime.fromisoformat(first[0]["ts"]))
    end = _hour(now)                      # the current hour is still in progress
    written, hour = 0, start
    while hour < end and written < max_hours:
        nxt = hour + timedelta(hours=1)
        lo, hi = hour.isoformat(), nxt.isoformat()
        points = store.read_snapshots(lo, hi)
        if points:
            store.write_hourly(lo, summarize_hour(points, store.read_samples(lo, hi)))
            written += 1
        hour = nxt
    return written


def series(hours: int) -> dict:
    hours = max(1, min(int(hours), 24 * 366 * 5))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    if hours > 24 * 30:
        # Beyond a month: the permanent hourly rollups (raw snapshots are purged after
        # SAMPLE_RETENTION_DAYS), bucketed down to MAX_POINTS
        points = [{k: v for k, v in p.items() if k != "machines"} for p in store.read_hourly(since)]
        bucket_s = 3600
        if len(points) > MAX_POINTS:
            bucket_s = max(3600, (hours * 3600) // MAX_POINTS)
            points = _bucket_avg(points, bucket_s)
        return {"hours": hours, "bucket_s": bucket_s, "points": points, "source": "hourly"}
    points = store.read_snapshots(since)
    bucket_s = SNAPSHOT_EVERY_S
    if len(points) > MAX_POINTS:
        bucket_s = max(SNAPSHOT_EVERY_S, (hours * 3600) // MAX_POINTS)
        points = _bucket_avg(points, bucket_s)
    return {"hours": hours, "bucket_s": bucket_s, "points": points}
