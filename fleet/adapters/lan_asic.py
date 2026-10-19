"""Home ASICs over the LAN via the cgminer-style API on TCP 4028.

Protocol (cgminer API-README): send a JSON object {"command": "<cmd>"} over TCP; the miner
replies with JSON and closes. Read-only commands like summary/pools need no auth.
Bitmain replies are often NUL-terminated and sometimes contain '}{' without a comma.
Hashrate keys vary by firmware ("GHS 5s", "MHS av", ...), so they are matched by pattern.
Stock firmware reports no power; watts are left for the view to estimate.
Units without a 4028 API (e.g. IceRiver KS5M, Elphapex web-only) report as unreachable
until their own driver is added.
"""

import json
import re
import socket

from fleet import settings
from fleet.adapters.base import Adapter
from fleet.units import to_hs

HASH_KEY = re.compile(r"^([KMGTP])HS (5s|av|30m|1m|5m|15m)$", re.IGNORECASE)
KEY_PREFERENCE = ("5s", "1m", "5m", "15m", "30m", "av")


def cgminer_command(host: str, port: int, command: str, timeout: float = 5.0) -> dict:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.sendall(json.dumps({"command": command}).encode())
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    text = b"".join(chunks).decode("utf-8", errors="replace").replace("\x00", "").strip()
    return parse_cgminer_json(text)


def parse_cgminer_json(text: str) -> dict:
    """Parse a cgminer reply, repairing Bitmain's missing commas between objects
    (inside arrays, or two top-level objects back to back)."""
    try:
        return json.loads(text)
    except ValueError:
        pass
    fixed = text.replace("}{", "},{")
    try:
        return json.loads(fixed)
    except ValueError:
        merged = {}
        for part in json.loads(f"[{fixed}]"):
            merged.update(part)
        return merged


def parse_hashrate(summary: dict) -> float | None:
    """Pick the freshest hashrate field and convert it to H/s."""
    found = {}
    for key, value in summary.items():
        m = HASH_KEY.match(key.strip())
        if m and value is not None:
            try:
                found[m.group(2).lower()] = to_hs(float(value), f"{m.group(1).upper()}H/s")
            except (TypeError, ValueError):
                continue
    for window in KEY_PREFERENCE:
        if window in found:
            return found[window]
    return None


class LanAsicAdapter(Adapter):
    name = "lan"
    interval_s = 60

    def is_configured(self) -> bool:
        return bool(settings.LAN_MINERS)

    def poll(self) -> dict:
        machines = []
        for name, address in settings.LAN_MINERS:
            host, _, port = address.partition(":")
            port = int(port or 4028)
            base = {
                "machine_key": f"lan:{name}",
                "source": self.name,
                "name": name,
                "kind": "asic",
                "location": "home",
            }
            try:
                summary = (cgminer_command(host, port, "summary").get("SUMMARY") or [{}])[0]
                hs = parse_hashrate(summary)
                pool = {}
                try:
                    pools = cgminer_command(host, port, "pools").get("POOLS") or []
                    active = [p for p in pools if p.get("Stratum Active")] or pools[:1]
                    if active:
                        pool = {"url": active[0].get("URL"), "user": active[0].get("User")}
                except Exception:
                    pass
                machines.append({
                    **base,
                    "running": (hs or 0) > 0,
                    "status": "hashing" if (hs or 0) > 0 else "no hashrate",
                    "hashrate_hs": hs,
                    "extra": {"ip": host, "elapsed_s": summary.get("Elapsed"), "pool": pool},
                })
            except (OSError, ValueError) as e:
                # One unreachable miner must not fail the whole source
                machines.append({**base, "running": None, "status": "not responding",
                                 "extra": {"ip": host, "error": str(e)[:200]}})
        return {"machines": machines, "accounts": {}}
