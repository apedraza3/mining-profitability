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


def take_snapshot():
    """Build the fleet view the way the web app does and store its compact totals."""
    from fleet.view import build_fleet
    from services.inventory_manager import InventoryManager

    data_dir = settings.BASE_DIR / "data"
    inv = InventoryManager(str(data_dir / "inventory.json"), str(data_dir / "locations.json"))
    fleet = build_fleet(inv.get_all_miners(), inv.get_all_locations())
    store.record_snapshot(compact(fleet))


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


def series(hours: int) -> dict:
    hours = max(1, min(int(hours), settings.SAMPLE_RETENTION_DAYS * 24))
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    points = store.read_snapshots(since)
    bucket_s = SNAPSHOT_EVERY_S
    if len(points) > MAX_POINTS:
        bucket_s = max(SNAPSHOT_EVERY_S, (hours * 3600) // MAX_POINTS)
        points = _bucket_avg(points, bucket_s)
    return {"hours": hours, "bucket_s": bucket_s, "points": points}
