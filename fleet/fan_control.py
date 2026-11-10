"""Exhaust fan control: set the AC Infinity fan speed from the hottest thing in the room.

Inputs (each mapped to 0..1 "heat demand" between a start and a full temperature):
- hot-aisle air from the controller probe (climate:basement)          FAN_AIR_F   = 80,95  (deg F)
- ASIC chip temps from the LAN reader (machine temp_max)              FAN_ASIC_C  = 70,85  (deg C)
- GPU core temps (machines whose extra carries gpu_temps_c)           FAN_GPU_C   = 70,83  (deg C)
Machines in FAN_EXCLUDE (default Z15: immersion-cooled) are ignored.

speed = FAN_MIN + demand x (FAN_MAX - FAN_MIN), on the controller's 0-10 scale.
- Up immediately; down one step at a time, only after FAN_DOWN_HOLD_S of lower demand.
- Every input stale (> FAN_STALE_S) -> FAN_MAX (fail safe: never cool less on missing data).
- A port in any mode other than "On" (e.g. Auto in the app) is left alone.

FAN_CONTROL: off (default) | dry_run (decide and show, never write) | on (write via Home
Assistant: number.<prefix>_port_<n>_on_power). The decision is stored as the
"fancontrol:basement" account so Fleet Now can show it.
"""

import logging
import time
from datetime import datetime, timezone

import requests

from fleet import settings, store
from fleet.adapters.homeassistant import parse_ports
from fleet.view import _age_s, norm

logger = logging.getLogger(__name__)

_state = {"last_change": 0.0, "lower_since": None}


def _pair(raw, default):
    try:
        a, b = (float(x) for x in raw.split(","))
        return (a, b) if b > a else default
    except (AttributeError, ValueError):
        return default


def ramp(value, start, full):
    if value is None:
        return None
    return max(0.0, min(1.0, (value - start) / (full - start)))


def collect_inputs(snapshot: dict, cfg: dict) -> list[dict]:
    """[{name, kind, value, unit, demand, stale}] from a store.read_all() snapshot."""
    out = []
    accounts = {a["account_key"]: a for a in snapshot.get("accounts", [])}
    climate = accounts.get("climate:basement")
    if climate:
        d = climate["data"]
        age = _age_s(climate.get("fetched_at"))
        out.append({"name": d.get("label") or "Hot aisle", "kind": "air", "value": d.get("temp_f"),
                    "unit": "°F", "demand": ramp(d.get("temp_f"), *cfg["air_f"]),
                    "stale": age is None or age > cfg["stale_s"]})
    excluded = {norm(n) for n in cfg["exclude"]}
    for key, acct in accounts.items():
        if not key.startswith("gputemps:"):
            continue
        d = acct["data"]
        if norm(d.get("name") or "") in excluded:
            continue
        temps = [t for t in d.get("temps_c") or [] if isinstance(t, (int, float))]
        age = _age_s(acct.get("fetched_at"))
        if not temps:  # read failed: show it as stale rather than silently dropping the rig
            out.append({"name": d.get("name"), "kind": "gpu", "value": None, "unit": "°C",
                        "demand": None, "stale": True})
            continue
        out.append({"name": d.get("name"), "kind": "gpu", "value": max(temps), "unit": "°C",
                    "demand": ramp(max(temps), *cfg["gpu_c"]),
                    "stale": age is None or age > cfg["stale_s"] or bool(d.get("error"))})
    for m in snapshot.get("machines", []):
        if norm(m["name"]) in excluded or (m.get("location") not in (None, "home") and m["source"] != "lan"):
            continue
        age = _age_s(m.get("fetched_at"))
        stale = age is None or age > cfg["stale_s"]
        gpu_temps = (m.get("extra") or {}).get("gpu_temps_c")
        if gpu_temps:
            hottest = max(t for t in gpu_temps if isinstance(t, (int, float)))
            out.append({"name": m["name"], "kind": "gpu", "value": hottest, "unit": "°C",
                        "demand": ramp(hottest, *cfg["gpu_c"]), "stale": stale})
        elif m["source"] == "lan" and m.get("temp_max") is not None and m.get("running") is not False:
            out.append({"name": m["name"], "kind": "asic", "value": m["temp_max"], "unit": "°C",
                        "demand": ramp(m["temp_max"], *cfg["asic_c"]), "stale": stale})
    return out


def decide(inputs, current, cfg, now, state):
    """Target speed (int 0-10), the driving input and a short reason. Pure; mutates state."""
    lo, hi = cfg["min"], cfg["max"]
    fresh = [i for i in inputs if not i["stale"] and i["demand"] is not None]
    if not fresh:
        state["lower_since"] = None
        return hi, None, "no fresh temperatures: fail safe to max"
    driver = max(fresh, key=lambda i: i["demand"])
    want = round(lo + driver["demand"] * (hi - lo))
    if current is None or want > current:
        state["lower_since"] = None
        return want, driver, "hotter: raise now" if current is not None else "first setting"
    if want == current:
        state["lower_since"] = None
        return current, driver, "steady"
    # Lower: wait until demand has stayed lower for the hold time, then one step down
    if state.get("lower_since") is None:
        state["lower_since"] = now
    if now - state["lower_since"] >= cfg["down_hold_s"]:
        state["lower_since"] = now
        return current - 1, driver, "cooler for a while: one step down"
    return current, driver, f"cooler: holding (step down after {cfg['down_hold_s'] // 60} min)"


def config() -> dict:
    return {
        "mode": (settings.FAN_CONTROL or "off").lower(),
        "min": max(0, min(10, settings.FAN_MIN)), "max": max(0, min(10, settings.FAN_MAX)),
        "air_f": _pair(settings.FAN_AIR_F, (80.0, 95.0)),
        "asic_c": _pair(settings.FAN_ASIC_C, (70.0, 85.0)),
        "gpu_c": _pair(settings.FAN_GPU_C, (70.0, 83.0)),
        "exclude": [n.strip() for n in settings.FAN_EXCLUDE.split(",") if n.strip()],
        "stale_s": settings.FAN_STALE_S, "down_hold_s": settings.FAN_DOWN_HOLD_S,
    }


def _set_speed(port: int, speed: int):
    url = f"{settings.HA_URL.rstrip('/')}/api/services/number/set_value"
    resp = requests.post(url, timeout=10, headers={"Authorization": f"Bearer {settings.HA_TOKEN}"},
                         json={"entity_id": f"number.{settings.HA_PREFIX}_port_{port}_on_power",
                               "value": speed})
    resp.raise_for_status()


def tick():
    cfg = config()
    if cfg["mode"] not in ("dry_run", "on") or not settings.HA_TOKEN:
        return
    snap = store.read_all()
    inputs = collect_inputs(snap, cfg)
    climate = next((a["data"] for a in snap["accounts"] if a["account_key"] == "climate:basement"), {})
    fans = {f["port"]: f for f in climate.get("fans") or []}
    ports = [p for p, _ in parse_ports(settings.HA_FAN_PORTS)]
    controllable = [p for p in ports if fans.get(p, {}).get("online") and fans[p].get("mode") == "On"]
    speeds = [fans[p]["speed"] for p in controllable if isinstance(fans[p].get("speed"), (int, float))]
    current = int(round(max(speeds))) if speeds else None

    target, driver, reason = decide(inputs, current, cfg, time.monotonic(), _state)
    target = max(cfg["min"], min(cfg["max"], int(target)))
    wrote, error = [], None
    if cfg["mode"] == "on" and controllable and target != current:
        try:
            for p in controllable:
                _set_speed(p, target)
                wrote.append(p)
            _state["last_change"] = time.monotonic()
        except Exception as e:  # Home Assistant down etc.: the controller keeps its last speed
            error = str(e)[:200]
            logger.warning("Fan speed write failed: %s", e)
    store.upsert_account("fancontrol:basement", "fancontrol", {
        "mode": cfg["mode"], "current": current, "target": target, "reason": reason,
        "driver": driver and {k: driver[k] for k in ("name", "kind", "value", "unit", "demand")},
        "inputs": inputs, "ports": ports, "controllable_ports": controllable, "wrote_ports": wrote,
        "error": error, "skipped_ports": [p for p in ports if p not in controllable],
        "limits": {"min": cfg["min"], "max": cfg["max"], "air_f": cfg["air_f"],
                   "asic_c": cfg["asic_c"], "gpu_c": cfg["gpu_c"], "exclude": cfg["exclude"]},
        "decided_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    })
