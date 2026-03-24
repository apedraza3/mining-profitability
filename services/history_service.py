"""Historical data tracking — profit snapshots and uptime logs (SQLite)."""

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "history.db"


class HistoryService:
    def __init__(self):
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS profit_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT NOT NULL,
                miner_name TEXT NOT NULL,
                algorithm TEXT,
                timestamp TEXT NOT NULL,
                daily_revenue REAL,
                daily_electricity REAL,
                daily_profit REAL,
                best_coin TEXT,
                hashrate REAL,
                hashrate_unit TEXT
            );

            CREATE TABLE IF NOT EXISTS uptime_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                online INTEGER NOT NULL,
                hashrate REAL,
                hashrate_units TEXT
            );

            CREATE TABLE IF NOT EXISTS peak_demand (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                billing_month TEXT NOT NULL UNIQUE,
                peak_kw REAL NOT NULL,
                recorded_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_profit_miner_time
                ON profit_snapshots(miner_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_uptime_miner_time
                ON uptime_logs(miner_id, timestamp);
        """)
        conn.commit()
        conn.close()

    def record_profit_snapshot(self, miner_results):
        """Record a profit snapshot for all miners. Self-throttles to once per hour."""
        conn = self._get_conn()
        now = datetime.now()

        # Check if we already have a snapshot within the last hour
        one_hour_ago = (now - timedelta(hours=1)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM profit_snapshots WHERE timestamp > ?",
            (one_hour_ago,),
        ).fetchone()
        if row["cnt"] > 0:
            conn.close()
            return False

        ts = now.isoformat()
        for r in miner_results:
            m = r["miner"]
            wtm = r.get("sources", {}).get("whattomine", {})
            conn.execute(
                """INSERT INTO profit_snapshots
                   (miner_id, miner_name, algorithm, timestamp,
                    daily_revenue, daily_electricity, daily_profit,
                    best_coin, hashrate, hashrate_unit)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    m["id"],
                    m["name"],
                    m.get("algorithm", ""),
                    ts,
                    r.get("daily_revenue", 0),
                    r.get("daily_electricity", 0),
                    r.get("best_daily_profit", 0),
                    wtm.get("best_coin", ""),
                    m.get("hashrate", 0),
                    m.get("hashrate_unit", ""),
                ),
            )

        conn.commit()
        conn.close()
        logger.info("Recorded profit snapshot for %d miners", len(miner_results))
        return True

    def record_uptime(self, statuses, miners):
        """Record uptime status for all matched miners from PowerPool data.
        Self-throttles to once per 4 minutes."""
        conn = self._get_conn()
        now = datetime.now()

        four_min_ago = (now - timedelta(minutes=4)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM uptime_logs WHERE timestamp > ?",
            (four_min_ago,),
        ).fetchone()
        if row["cnt"] > 0:
            conn.close()
            return False

        ts = now.isoformat()
        for miner in miners:
            miner_id = miner["id"]
            status = statuses.get(miner_id)
            if status is not None:
                conn.execute(
                    """INSERT INTO uptime_logs
                       (miner_id, timestamp, online, hashrate, hashrate_units)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        miner_id,
                        ts,
                        1 if status["online"] else 0,
                        status.get("hashrate", 0),
                        status.get("hashrate_units", ""),
                    ),
                )

        conn.commit()
        conn.close()
        return True

    def get_profit_history(self, days=30, miner_id=None):
        """Get daily profit history, aggregated by day."""
        conn = self._get_conn()
        since = (datetime.now() - timedelta(days=days)).isoformat()

        if miner_id:
            rows = conn.execute(
                """SELECT miner_id, miner_name,
                          DATE(timestamp) as day,
                          AVG(daily_profit) as avg_profit,
                          AVG(daily_revenue) as avg_revenue,
                          AVG(daily_electricity) as avg_electricity
                   FROM profit_snapshots
                   WHERE timestamp > ? AND miner_id = ?
                   GROUP BY miner_id, DATE(timestamp)
                   ORDER BY day""",
                (since, miner_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT miner_id, miner_name,
                          DATE(timestamp) as day,
                          AVG(daily_profit) as avg_profit,
                          AVG(daily_revenue) as avg_revenue,
                          AVG(daily_electricity) as avg_electricity
                   FROM profit_snapshots
                   WHERE timestamp > ?
                   GROUP BY miner_id, DATE(timestamp)
                   ORDER BY day""",
                (since,),
            ).fetchall()

        # Per-miner breakdown
        miners = {}
        for row in rows:
            mid = row["miner_id"]
            if mid not in miners:
                miners[mid] = {"name": row["miner_name"], "data": []}
            miners[mid]["data"].append(
                {
                    "day": row["day"],
                    "profit": round(row["avg_profit"], 2),
                    "revenue": round(row["avg_revenue"], 2),
                    "electricity": round(row["avg_electricity"], 2),
                }
            )

        # Fleet total per day
        fleet_rows = conn.execute(
            """SELECT DATE(timestamp) as day,
                      SUM(daily_profit) as total_profit,
                      SUM(daily_revenue) as total_revenue,
                      SUM(daily_electricity) as total_electricity,
                      COUNT(DISTINCT miner_id) as miner_count
               FROM profit_snapshots
               WHERE timestamp > ?
               GROUP BY DATE(timestamp)
               ORDER BY day""",
            (since,),
        ).fetchall()
        conn.close()

        fleet_total = [
            {
                "day": r["day"],
                "profit": round(r["total_profit"], 2),
                "revenue": round(r["total_revenue"], 2),
                "electricity": round(r["total_electricity"], 2),
                "miner_count": r["miner_count"],
            }
            for r in fleet_rows
        ]

        return {"miners": miners, "fleet_total": fleet_total}

    def get_uptime_stats(self, days=7):
        """Get uptime percentage per miner over the last N days."""
        conn = self._get_conn()
        since = (datetime.now() - timedelta(days=days)).isoformat()

        rows = conn.execute(
            """SELECT miner_id,
                      COUNT(*) as total_checks,
                      SUM(online) as online_checks,
                      AVG(CASE WHEN online = 1 THEN hashrate ELSE NULL END) as avg_hashrate
               FROM uptime_logs
               WHERE timestamp > ?
               GROUP BY miner_id""",
            (since,),
        ).fetchall()
        conn.close()

        return {
            row["miner_id"]: {
                "total_checks": row["total_checks"],
                "online_checks": row["online_checks"],
                "uptime_pct": round(
                    row["online_checks"] / row["total_checks"] * 100, 1
                )
                if row["total_checks"] > 0
                else 0,
                "avg_hashrate": round(row["avg_hashrate"] or 0, 2),
            }
            for row in rows
        }

    def update_peak_demand(self, current_kw):
        """Store the highest observed peak demand for the current billing month.
        Only updates if the new reading is higher than what's stored."""
        if not current_kw or current_kw <= 0:
            return None
        conn = self._get_conn()
        billing_month = datetime.now().strftime("%Y-%m")
        now = datetime.now().isoformat()

        row = conn.execute(
            "SELECT peak_kw FROM peak_demand WHERE billing_month = ?",
            (billing_month,),
        ).fetchone()

        if row is None:
            # First reading this month
            conn.execute(
                "INSERT INTO peak_demand (billing_month, peak_kw, recorded_at) VALUES (?, ?, ?)",
                (billing_month, round(current_kw, 2), now),
            )
            conn.commit()
            peak = current_kw
            logger.info("Peak demand for %s initialized at %.2f kW", billing_month, current_kw)
        elif current_kw > row["peak_kw"]:
            # New high — update
            conn.execute(
                "UPDATE peak_demand SET peak_kw = ?, recorded_at = ? WHERE billing_month = ?",
                (round(current_kw, 2), now, billing_month),
            )
            conn.commit()
            peak = current_kw
            logger.info("Peak demand for %s updated: %.2f → %.2f kW", billing_month, row["peak_kw"], current_kw)
        else:
            # Current reading is lower — keep the stored high
            peak = row["peak_kw"]

        conn.close()
        return round(peak, 2)

    def get_peak_demand(self, billing_month=None):
        """Get the stored peak demand for a billing month (defaults to current)."""
        if not billing_month:
            billing_month = datetime.now().strftime("%Y-%m")
        conn = self._get_conn()
        row = conn.execute(
            "SELECT peak_kw, recorded_at FROM peak_demand WHERE billing_month = ?",
            (billing_month,),
        ).fetchone()
        conn.close()
        if row:
            return {"peak_kw": row["peak_kw"], "recorded_at": row["recorded_at"]}
        return None

    def cleanup_old_data(self):
        """Remove old data — uptime: 90 days, profit: 365 days. VACUUM to reclaim space."""
        conn = self._get_conn()
        uptime_cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        profit_cutoff = (datetime.now() - timedelta(days=365)).isoformat()
        up_deleted = conn.execute("DELETE FROM uptime_logs WHERE timestamp < ?", (uptime_cutoff,)).rowcount
        prof_deleted = conn.execute(
            "DELETE FROM profit_snapshots WHERE timestamp < ?", (profit_cutoff,)
        ).rowcount
        conn.commit()
        if up_deleted > 0 or prof_deleted > 0:
            conn.execute("VACUUM")
            logger.info("Cleaned up %d uptime + %d profit rows, VACUUM complete", up_deleted, prof_deleted)
        conn.close()
