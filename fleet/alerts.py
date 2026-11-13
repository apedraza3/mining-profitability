"""Fleet Now alerts: tell the owner when something needs attention.

Runs in the poller every ALERT_EVERY_S. Each rule yields (key, message) for every problem
it sees right now. A problem has to persist for its hold time before it alerts (so a single
missed poll doesn't page anyone), re-alerts every REMIND_S while it lasts, and sends a
"resolved" message when it clears.

Channels come from the main dashboard's Alert settings (history.db alert_config: Telegram
bot token + chat id, or Discord webhook), so there's one place to configure them. With no
channel configured the alerts still show on Fleet Now ("alerts:active").
"""

import logging
import sqlite3
import time

import requests

from fleet import settings, store

logger = logging.getLogger(__name__)

ALERT_EVERY_S = 120
REMIND_S = 6 * 3600
HISTORY_DB = settings.BASE_DIR / "data" / "history.db"

# Hold times (s) before alerting, by rule
HOLD = {"down": 600, "temp": 300, "rental": 120, "source": 900, "fan": 300, "demand": 240}

# Limits; deliberately above normal operating temps (see the fan controller for the ramps)
LIMITS = {"gpu_core": 85.0, "gpu_hotspot": 102.0, "gpu_mem": 90.0, "asic": 92.0, "air_f": 100.0}

_state: dict[str, dict] = {}   # key -> {"since", "sent_at", "message"}


def _problems(fleet: dict, snap: dict) -> list[tuple[str, str, str]]:
    """[(key, rule, message)] for everything wrong right now."""
    out = []
    support = ("overhead", "unattributed", "non_mining")
    for r in fleet.get("machines") or []:
        if r.get("kind") in support or r.get("inventory_status") == "inactive":
            continue
        if r.get("sources") == ["inventory"]:
            continue  # no live source by design (e.g. CircleHash)
        if r.get("running") is False:
            out.append((f"down:{r['name']}", "down", f"{r['name']} is not running ({r.get('status')})"))
        elif r.get("stale_sources") and set(r["stale_sources"]) >= set(r.get("sources") or []):
            out.append((f"down:{r['name']}", "down",
                        f"{r['name']}: no fresh data from {', '.join(r['stale_sources'])}"))
        if r.get("kind") == "rental_server" and "clore" in (r.get("sources") or []):
            status = (r.get("status") or "").lower()
            if "not rented" in status or status == "offline":
                out.append((f"rental:{r['name']}", "rental", f"Clore rig {r['name'][:24]}: {r.get('status')}"))

    accounts = {a["account_key"]: a["data"] for a in snap.get("accounts", [])}
    for key, d in accounts.items():
        if key.startswith("gputemps:") and not d.get("error"):
            name = d.get("name")
            for field, limit_key, label in (("temps_c", "gpu_core", "core"),
                                            ("hotspot_temps_c", "gpu_hotspot", "hotspot"),
                                            ("mem_temps_c", "gpu_mem", "memory")):
                vals = [(i, t) for i, t in enumerate(d.get(field) or []) if isinstance(t, (int, float))]
                hot = [(i, t) for i, t in vals if t >= LIMITS[limit_key]]
                if hot:
                    worst = max(hot, key=lambda x: x[1])
                    out.append((f"temp:{name}:{label}", "temp",
                                f"{name} GPU {worst[0]} {label} {worst[1]:.0f} °C "
                                f"(limit {LIMITS[limit_key]:.0f})"))
    for m in snap.get("machines", []):
        if m["source"] == "lan" and isinstance(m.get("temp_max"), (int, float)) and m["temp_max"] >= LIMITS["asic"]:
            out.append((f"temp:{m['name']}", "temp", f"{m['name']} chips {m['temp_max']:.0f} °C "
                                                      f"(limit {LIMITS['asic']:.0f})"))
    climate = accounts.get("climate:basement") or {}
    if isinstance(climate.get("temp_f"), (int, float)) and climate["temp_f"] >= LIMITS["air_f"]:
        out.append(("temp:air", "temp", f"{climate.get('label') or 'Hot aisle'} {climate['temp_f']:.0f} °F "
                                        f"(limit {LIMITS['air_f']:.0f})"))

    for s in fleet.get("sources") or []:
        if s.get("configured") and s.get("state") in ("error", "stale"):
            out.append((f"source:{s['source']}", "source",
                        f"Data source {s['source']} is {s['state']}: {(s.get('last_error') or '')[:120]}"))
    dm = (fleet.get("summary") or {}).get("demand") or {}
    if dm.get("status") in ("close", "over") and dm.get("minutes_into_hour", 0) >= 10:
        out.append(("demand:hour", "demand",
                    f"Demand: this hour is on track for {dm['projected_kw']:.1f} kW vs this month's "
                    f"{'4-8 PM ' if dm.get('which_peak') == 'onpeak' else ''}peak {dm['peak_kw']:.1f} kW "
                    f"(${dm.get('demand_rate_kw_month') or 0:.2f}/kW-month). Hold off big loads "
                    f"(EV charging, dryer, oven, extra rigs) until the hour ends."))
    fan = accounts.get("fancontrol:basement") or {}
    if fan.get("mode") == "on" and fan.get("error"):
        out.append(("fan:write", "fan", f"Fan control can't set the fans: {fan['error'][:120]}"))
    return out


def channels() -> list[dict]:
    try:
        db = sqlite3.connect(f"file:{HISTORY_DB}?mode=ro", uri=True, timeout=5)
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute("SELECT * FROM alert_config WHERE enabled = 1")]
        db.close()
        return rows
    except sqlite3.Error:
        return []


def send(text: str, chans: list[dict]) -> list[str]:
    errors = []
    for c in chans:
        try:
            if c.get("channel") == "telegram" and c.get("bot_token") and c.get("chat_id"):
                requests.post(f"https://api.telegram.org/bot{c['bot_token']}/sendMessage", timeout=10,
                              json={"chat_id": c["chat_id"], "text": text}).raise_for_status()
            elif c.get("channel") == "discord" and c.get("webhook_url"):
                requests.post(c["webhook_url"], timeout=10, json={"content": text}).raise_for_status()
        except Exception as e:  # report but never raise: alerts must not stop polling
            errors.append(f"{c.get('channel')}: {str(e)[:120]}")
    return errors


def evaluate(problems, now, state, hold=HOLD, remind_s=REMIND_S):
    """Pure: returns (messages_to_send, active_list) and updates state."""
    current = {k: (rule, msg) for k, rule, msg in problems}
    messages = []
    for key, (rule, msg) in current.items():
        st = state.setdefault(key, {"since": now, "sent_at": None, "message": msg, "rule": rule})
        st["message"] = msg
        due = now - st["since"] >= hold.get(rule, 300)
        if due and (st["sent_at"] is None or now - st["sent_at"] >= remind_s):
            messages.append(("⚠️ " if st["sent_at"] is None else "⏰ still: ") + msg)
            st["sent_at"] = now
    for key in [k for k in state if k not in current]:
        st = state.pop(key)
        if st["sent_at"] is not None:
            messages.append("✅ resolved: " + st["message"])
    active = [{"key": k, "message": v["message"], "since_s": round(now - v["since"]),
               "alerted": v["sent_at"] is not None} for k, v in state.items()]
    return messages, active


def tick(fleet: dict | None = None):
    from fleet.history import _build_with_inventory
    fleet = fleet or _build_with_inventory()
    snap = store.read_all()
    messages, active = evaluate(_problems(fleet, snap), time.monotonic(), _state)
    chans = channels()
    errors = send("Fleet Now\n" + "\n".join(messages), chans) if messages and chans else []
    store.upsert_account("alerts:active", "alerts", {
        "active": active, "channels": [c.get("channel") for c in chans],
        "last_sent": messages if chans else [], "send_errors": errors,
    })
