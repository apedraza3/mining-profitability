import csv
import io
import json
import logging
import re
from datetime import datetime
from pathlib import Path

import config

logger = logging.getLogger(__name__)

POWER_IMPORT_FILE = config.DATA_DIR / "power_imports.json"


def _load_power_data() -> dict:
    if POWER_IMPORT_FILE.exists():
        with open(POWER_IMPORT_FILE, "r") as f:
            return json.load(f)
    return {"miners": {}, "last_import": None}


def _save_power_data(data: dict) -> None:
    with open(POWER_IMPORT_FILE, "w") as f:
        json.dump(data, f, indent=2)


def parse_power_csv(csv_content: str) -> dict:
    """Parse a power report CSV (e.g. from Foreman, or any tool with a compatible format).

    CSV format:
      miner_id, miner_name, miner_mac, miner_serial, pickaxe_id, pickaxe_name,
      client_id, client_name, <date>_uptime, <date>_power_draw, <date>_power_cost, ...

    The date columns repeat for each day in the report. Power draw is in watt-hours.

    Returns dict with per-miner stats:
    {
        "miners": {
            "<miner_name>": {
                "miner_id": "...",
                "miner_name": "...",
                "avg_power_draw_wh": 85000.0,
                "avg_power_watts": 3541.7,
                "avg_uptime_pct": 98.5,
                "total_power_cost": 45.20,
                "days_in_report": 7,
                "daily_readings": [...]
            }
        },
        "import_date": "2026-03-03T...",
        "report_days": 7
    }
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    if not reader.fieldnames:
        return {"error": "Empty or invalid CSV"}

    # Find date-based columns: pattern like "2026-03-01_power_consumption"
    date_pattern = re.compile(
        r"(\d{4}-\d{2}-\d{2})_(uptime|power_draw|power_consumption|power_cost|theoretical_hash_rate)"
    )
    dates = set()
    for col in reader.fieldnames:
        m = date_pattern.match(col)
        if m:
            dates.add(m.group(1))
    dates = sorted(dates)

    # Detect which power column name is used (power_draw vs power_consumption)
    power_col = "power_draw"
    if dates:
        test_date = dates[0]
        if f"{test_date}_power_consumption" in reader.fieldnames:
            power_col = "power_consumption"

    miners = {}
    for row in reader:
        miner_name = (row.get("miner_name") or "").strip()
        miner_id = (row.get("miner_id") or "").strip()
        if not miner_name:
            continue

        daily_readings = []
        total_power_draw = 0
        total_uptime = 0
        total_cost = 0
        days_with_data = 0

        for date in dates:
            power_draw = _safe_float(row.get(f"{date}_{power_col}"))
            uptime = _safe_float(row.get(f"{date}_uptime"))
            cost = _safe_float(row.get(f"{date}_power_cost"))

            daily_readings.append({
                "date": date,
                "power_draw_wh": power_draw,
                "uptime_pct": uptime,
                "power_cost": cost,
            })

            if power_draw > 0:
                total_power_draw += power_draw
                total_uptime += uptime
                total_cost += cost
                days_with_data += 1

        avg_power_wh = total_power_draw / days_with_data if days_with_data > 0 else 0
        # Convert watt-hours to average watts: Wh / 24h = avg watts
        avg_watts = avg_power_wh / 24 if avg_power_wh > 0 else 0
        avg_uptime = total_uptime / days_with_data if days_with_data > 0 else 0

        # Extract extra metadata for inventory creation
        miner_type = (row.get("miner_type") or "").strip()
        theoretical_hr = _safe_float(row.get(f"{dates[0]}_theoretical_hash_rate")) if dates else 0

        miners[miner_name] = {
            "miner_id": miner_id,
            "miner_name": miner_name,
            "miner_type": miner_type,
            "theoretical_hash_rate": theoretical_hr,
            "avg_power_draw_wh": round(avg_power_wh, 2),
            "avg_power_watts": round(avg_watts, 2),
            "avg_uptime_pct": round(avg_uptime, 2),
            "total_power_cost": round(total_cost, 2),
            "days_in_report": days_with_data,
            "daily_readings": daily_readings,
        }

    return {
        "miners": miners,
        "import_date": datetime.now().isoformat(),
        "report_days": len(dates),
    }


def import_power_csv(csv_content: str) -> dict:
    """Parse and save power report CSV data."""
    parsed = parse_power_csv(csv_content)
    if "error" in parsed:
        return parsed

    data = _load_power_data()
    # Merge — update existing miners, add new ones
    data["miners"].update(parsed["miners"])
    data["last_import"] = parsed["import_date"]
    _save_power_data(data)

    return {
        "imported": len(parsed["miners"]),
        "total_stored": len(data["miners"]),
        "report_days": parsed["report_days"],
        "import_date": parsed["import_date"],
    }


def get_power_data() -> dict:
    """Get all stored power import data."""
    return _load_power_data()


def get_miner_actual_watts(miner_name: str) -> float | None:
    """Look up actual wattage for a miner from CSV imports.
    Tries exact match first, then fuzzy substring match."""
    data = _load_power_data()
    miners = data.get("miners", {})

    # Exact match
    if miner_name in miners:
        watts = miners[miner_name].get("avg_power_watts", 0)
        return watts if watts > 0 else None

    # Substring match (imported names often include extra info)
    name_lower = miner_name.lower()
    for stored_name, info in miners.items():
        if name_lower in stored_name.lower() or stored_name.lower() in name_lower:
            watts = info.get("avg_power_watts", 0)
            return watts if watts > 0 else None

    return None


def clear_power_data() -> None:
    """Clear all stored power import data."""
    _save_power_data({"miners": {}, "last_import": None})


def _safe_float(val) -> float:
    if val is None:
        return 0.0
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0
