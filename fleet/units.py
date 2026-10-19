"""Hashrate unit handling. Everything is stored in H/s."""

import re

_PREFIX = {"": 1, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18}


def unit_multiplier(unit: str) -> float | None:
    """'TH/s' -> 1e12, 'MSol/s' -> 1e6, 'KH/s' -> 1e3. None if unrecognised."""
    m = re.fullmatch(r"\s*([KMGTPE]?)(H|Sol)/s\s*", unit or "", re.IGNORECASE)
    if not m:
        return None
    return _PREFIX[m.group(1).upper()]


def to_hs(value, unit: str) -> float | None:
    mult = unit_multiplier(unit)
    if value is None or mult is None:
        return None
    return float(value) * mult


def pay_rate_per_hs(rate: float, units: str) -> float | None:
    """PowerPool payRateUnits like 'BTC/EH/s' (per day) -> BTC per day per 1 H/s."""
    m = re.fullmatch(r"\s*BTC/([KMGTPE]?(?:H|Sol)/s)\s*", units or "", re.IGNORECASE)
    if not m:
        return None
    mult = unit_multiplier(m.group(1))
    return rate / mult if mult else None


def format_hs(hs: float | None) -> str:
    if hs is None:
        return "—"
    for prefix in ("E", "P", "T", "G", "M", "K"):
        if hs >= _PREFIX[prefix]:
            return f"{hs / _PREFIX[prefix]:.2f} {prefix}H/s"
    return f"{hs:.0f} H/s"
