"""Basement climate and exhaust fans from Home Assistant (AC Infinity Controller 69 Pro).

Read-only: GET {HA_URL}/api/states/<entity_id> with a long-lived access token
(Home Assistant -> your profile -> Security -> Long-lived access tokens).
Configure in fleet.env:
  HA_URL=http://127.0.0.1:8123            (default; poller runs with host networking)
  HA_TOKEN=<long-lived token>
  HA_PREFIX=new_device                    (the AC Infinity device's entity prefix)
  HA_FAN_PORTS=1:S8 exhaust A,2:S8 exhaust B    (port:label for ports with fans)
The fans are never changed from here; Home Assistant / the AC Infinity app control them.
"""

import math

from fleet import settings
from fleet.adapters.base import Adapter


def _num(state):
    try:
        return float(state)
    except (TypeError, ValueError):
        return None


def to_celsius(value, unit):
    if value is None:
        return None
    return (value - 32) * 5 / 9 if (unit or "").upper().endswith("F") else value


def dew_point_c(temp_c, rh):
    """Magnus formula; good to ~0.4 C for normal indoor ranges."""
    if temp_c is None or not rh or rh <= 0:
        return None
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100) + a * temp_c / (b + temp_c)
    return b * gamma / (a - gamma)


def parse_ports(raw: str) -> list[tuple[int, str]]:
    out = []
    for item in (raw or "").split(","):
        port, _, label = item.strip().partition(":")
        if port.strip().isdigit():
            out.append((int(port), label.strip() or f"Port {port.strip()}"))
    return out


class HomeAssistantAdapter(Adapter):
    name = "homeassistant"
    interval_s = 60

    def is_configured(self) -> bool:
        return bool(settings.HA_TOKEN)

    def _state(self, entity_id):
        return self.get_json(f"{settings.HA_URL.rstrip('/')}/api/states/{entity_id}",
                             headers={"Authorization": f"Bearer {settings.HA_TOKEN}"})

    def _maybe(self, entity_id):
        try:
            return self._state(entity_id)
        except Exception:
            return None

    def poll(self) -> dict:
        p = settings.HA_PREFIX
        temp = self._state(f"sensor.{p}_temperature")      # must work, else the source errors
        hum = self._maybe(f"sensor.{p}_humidity") or {}
        unit = (temp.get("attributes") or {}).get("unit_of_measurement") or "°F"
        temp_c = to_celsius(_num(temp.get("state")), unit)
        rh = _num(hum.get("state"))
        dew_c = dew_point_c(temp_c, rh)

        fans = []
        for port, label in parse_ports(settings.HA_FAN_PORTS):
            power = self._maybe(f"sensor.{p}_port_{port}_current_power") or {}
            mode = self._maybe(f"select.{p}_port_{port}_active_mode") or {}
            online = self._maybe(f"binary_sensor.{p}_port_{port}_status") or {}
            fans.append({
                "port": port, "label": label,
                "speed": _num(power.get("state")),           # 0-10 on AC Infinity controllers
                "mode": mode.get("state"),
                "online": online.get("state") == "on",
            })
        climate = {
            "label": settings.HA_PROBE_LABEL,
            "temp_c": round(temp_c, 1) if temp_c is not None else None,
            "temp_f": round(temp_c * 9 / 5 + 32, 1) if temp_c is not None else None,
            "humidity_pct": round(rh, 1) if rh is not None else None,
            "dew_point_c": round(dew_c, 1) if dew_c is not None else None,
            "dew_point_f": round(dew_c * 9 / 5 + 32, 1) if dew_c is not None else None,
            "fans": fans,
            "measured_at": temp.get("last_updated"),
        }
        return {"machines": [], "accounts": {"climate:basement": climate}}
