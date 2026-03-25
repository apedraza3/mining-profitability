"""Time-of-Use (TOU) electricity rate service.

Manages TOU schedules per location and calculates current/weighted rates.
Each location can have multiple rate periods defined by hour ranges and
day-of-week applicability.
"""

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "history.db"


class TOUService:
    def __init__(self, history_svc):
        self.history_svc = history_svc
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tou_schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                location_id TEXT NOT NULL,
                label TEXT NOT NULL DEFAULT '',
                start_hour INTEGER NOT NULL,
                end_hour INTEGER NOT NULL,
                rate REAL NOT NULL,
                days TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_tou_location
                ON tou_schedules(location_id);
        """)
        conn.commit()
        conn.close()

    def get_schedules(self, location_id: str) -> list[dict]:
        """Return all TOU schedule periods for a location."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM tou_schedules WHERE location_id = ? ORDER BY start_hour",
            (location_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_schedules(self, location_id: str, periods: list[dict]) -> list[dict]:
        """Delete existing periods and insert new ones for a location.

        Each period dict: {label, start_hour, end_hour, rate, days}
        days is a comma-separated string of day-of-week numbers (0=Mon, 6=Sun).
        """
        conn = self._get_conn()
        conn.execute(
            "DELETE FROM tou_schedules WHERE location_id = ?",
            (location_id,),
        )

        now = datetime.now().isoformat()
        for p in periods:
            start_hour = int(p.get("start_hour", 0))
            end_hour = int(p.get("end_hour", 24))
            rate = float(p.get("rate", 0))
            label = p.get("label", "")
            days = p.get("days", "0,1,2,3,4,5,6")

            if start_hour < 0 or start_hour > 23:
                continue
            if end_hour < 1 or end_hour > 24:
                continue
            if rate < 0:
                continue

            conn.execute(
                """INSERT INTO tou_schedules
                   (location_id, label, start_hour, end_hour, rate, days, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (location_id, label, start_hour, end_hour, rate, days, now),
            )

        conn.commit()
        conn.close()
        return self.get_schedules(location_id)

    def delete_schedules(self, location_id: str) -> bool:
        """Delete all TOU schedules for a location."""
        conn = self._get_conn()
        deleted = conn.execute(
            "DELETE FROM tou_schedules WHERE location_id = ?",
            (location_id,),
        ).rowcount
        conn.commit()
        conn.close()
        return deleted > 0

    def get_current_rate(self, location_id: str) -> float | None:
        """Get the currently active TOU rate based on current hour and day of week.

        Returns None if no TOU schedule exists (caller should fall back to flat rate).
        Day of week: 0=Monday, 6=Sunday (Python convention).
        """
        schedules = self.get_schedules(location_id)
        if not schedules:
            return None

        now = datetime.now()
        current_hour = now.hour
        current_dow = now.weekday()  # 0=Mon, 6=Sun

        for period in schedules:
            days_str = period.get("days", "0,1,2,3,4,5,6")
            try:
                allowed_days = [int(d.strip()) for d in days_str.split(",")]
            except (ValueError, AttributeError):
                allowed_days = list(range(7))

            if current_dow not in allowed_days:
                continue

            start = period["start_hour"]
            end = period["end_hour"]

            # Handle ranges like 22-6 (overnight) vs 9-17
            if start < end:
                if start <= current_hour < end:
                    return period["rate"]
            else:
                # Overnight: e.g. start=22, end=6 means 22-23 and 0-5
                if current_hour >= start or current_hour < end:
                    return period["rate"]

        # Current hour not covered by any period — return None to use flat rate
        return None

    def get_weighted_daily_rate(self, location_id: str) -> float | None:
        """Calculate the 24-hour weighted average $/kWh across all TOU periods.

        Returns None if no TOU schedule exists.
        Uses the current day of week for rate selection.
        """
        schedules = self.get_schedules(location_id)
        if not schedules:
            return None

        current_dow = datetime.now().weekday()

        # Build hour-to-rate map for the current day
        hour_rates = {}
        for period in schedules:
            days_str = period.get("days", "0,1,2,3,4,5,6")
            try:
                allowed_days = [int(d.strip()) for d in days_str.split(",")]
            except (ValueError, AttributeError):
                allowed_days = list(range(7))

            if current_dow not in allowed_days:
                continue

            start = period["start_hour"]
            end = period["end_hour"]
            rate = period["rate"]

            if start < end:
                for h in range(start, end):
                    hour_rates[h] = rate
            else:
                # Overnight span
                for h in range(start, 24):
                    hour_rates[h] = rate
                for h in range(0, end):
                    hour_rates[h] = rate

        if not hour_rates:
            return None

        # For hours not covered, the caller's flat rate will apply,
        # but for weighted average we only count covered hours
        total_rate = sum(hour_rates.values())
        covered_hours = len(hour_rates)

        if covered_hours < 24:
            # Return the average of covered hours only
            # The caller should blend this with flat rate for uncovered hours
            return round(total_rate / covered_hours, 6)

        return round(total_rate / 24, 6)
