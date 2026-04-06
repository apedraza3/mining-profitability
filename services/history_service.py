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

            CREATE TABLE IF NOT EXISTS pool_payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT,
                coin TEXT NOT NULL,
                amount REAL NOT NULL,
                fiat_value_usd REAL,
                wallet_address TEXT,
                tx_hash TEXT,
                payout_date TEXT NOT NULL,
                pool_name TEXT,
                recorded_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_payouts_date ON pool_payouts(payout_date);

            CREATE TABLE IF NOT EXISTS miner_roi_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT NOT NULL UNIQUE,
                hardware_cost REAL NOT NULL DEFAULT 0,
                total_earned REAL NOT NULL DEFAULT 0,
                total_electricity_paid REAL NOT NULL DEFAULT 0,
                first_tracked_date TEXT,
                last_updated TEXT
            );

            CREATE TABLE IF NOT EXISTS alert_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                webhook_url TEXT,
                bot_token TEXT,
                chat_id TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                alert_offline INTEGER NOT NULL DEFAULT 1,
                alert_hashrate_drop INTEGER NOT NULL DEFAULT 1,
                alert_negative_profit INTEGER NOT NULL DEFAULT 1,
                alert_daily_summary INTEGER NOT NULL DEFAULT 1,
                hashrate_drop_pct REAL NOT NULL DEFAULT 20.0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                miner_id TEXT,
                message TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                channel TEXT NOT NULL
            );

            -- auto_pause_config is owned by pdu_service.py
            -- tou_schedules is owned by tou_service.py
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

    # ---- Payout tracking ----

    def record_payout(self, miner_id, coin, amount, fiat_value_usd=None,
                      wallet_address=None, tx_hash=None, payout_date=None,
                      pool_name=None):
        """Record a pool payout."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        if not payout_date:
            payout_date = datetime.now().strftime("%Y-%m-%d")
        conn.execute(
            """INSERT INTO pool_payouts
               (miner_id, coin, amount, fiat_value_usd, wallet_address,
                tx_hash, payout_date, pool_name, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (miner_id, coin, amount, fiat_value_usd, wallet_address,
             tx_hash, payout_date, pool_name, now),
        )
        conn.commit()
        conn.close()
        logger.info("Recorded payout: %s %s for miner %s", amount, coin, miner_id)

    def get_payouts(self, days=90, coin=None, miner_id=None):
        """Get payouts within the last N days, optionally filtered."""
        conn = self._get_conn()
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        query = "SELECT * FROM pool_payouts WHERE payout_date >= ?"
        params = [since]
        if coin:
            query += " AND coin = ?"
            params.append(coin)
        if miner_id:
            query += " AND miner_id = ?"
            params.append(miner_id)
        query += " ORDER BY payout_date DESC"
        rows = conn.execute(query, params).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_payout_summary(self):
        """Get aggregated payout summary by coin."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT coin,
                      COUNT(*) as payout_count,
                      SUM(amount) as total_amount,
                      SUM(fiat_value_usd) as total_usd,
                      MIN(payout_date) as first_payout,
                      MAX(payout_date) as last_payout
               FROM pool_payouts
               GROUP BY coin
               ORDER BY total_usd DESC"""
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # ---- ROI tracking ----

    def get_cumulative_earnings(self, miner_id):
        """SUM(daily_revenue) from profit_snapshots for a miner."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(daily_revenue), 0) as total FROM profit_snapshots WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()
        conn.close()
        return round(row["total"], 2)

    def get_cumulative_electricity(self, miner_id):
        """SUM(daily_electricity) from profit_snapshots for a miner."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT COALESCE(SUM(daily_electricity), 0) as total FROM profit_snapshots WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()
        conn.close()
        return round(row["total"], 2)

    def get_roi_data(self, miner_id):
        """Get ROI data: cumulative earnings, electricity, and hardware cost."""
        conn = self._get_conn()
        # Get tracking record
        row = conn.execute(
            "SELECT * FROM miner_roi_tracking WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()

        # Also get cumulative from snapshots
        earnings = self.get_cumulative_earnings(miner_id)
        electricity = self.get_cumulative_electricity(miner_id)

        hardware_cost = 0
        if row:
            hardware_cost = row["hardware_cost"]
            # Add any tracked deltas
            earnings += row["total_earned"]
            electricity += row["total_electricity_paid"]

        conn.close()
        net_profit = earnings - electricity
        roi_pct = round((net_profit / hardware_cost) * 100, 1) if hardware_cost > 0 else 0
        return {
            "miner_id": miner_id,
            "hardware_cost": hardware_cost,
            "total_earned": round(earnings, 2),
            "total_electricity": round(electricity, 2),
            "net_profit": round(net_profit, 2),
            "roi_pct": roi_pct,
            "first_tracked_date": row["first_tracked_date"] if row else None,
            "last_updated": row["last_updated"] if row else None,
        }

    def update_roi_tracking(self, miner_id, earned_delta=0, electricity_delta=0):
        """Increment ROI tracking for a miner (used for manual adjustments)."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        row = conn.execute(
            "SELECT * FROM miner_roi_tracking WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE miner_roi_tracking
                   SET total_earned = total_earned + ?,
                       total_electricity_paid = total_electricity_paid + ?,
                       last_updated = ?
                   WHERE miner_id = ?""",
                (earned_delta, electricity_delta, now, miner_id),
            )
        else:
            conn.execute(
                """INSERT INTO miner_roi_tracking
                   (miner_id, hardware_cost, total_earned, total_electricity_paid,
                    first_tracked_date, last_updated)
                   VALUES (?, 0, ?, ?, ?, ?)""",
                (miner_id, earned_delta, electricity_delta, now, now),
            )
        conn.commit()
        conn.close()

    # ---- Alert config helpers ----

    def get_alert_configs(self):
        """Get all alert configurations."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM alert_config ORDER BY id").fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def save_alert_config(self, channel, webhook_url=None, bot_token=None,
                          chat_id=None, settings=None):
        """Upsert an alert config for a channel (telegram or discord)."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        if settings is None:
            settings = {}

        # Check if config for this channel exists
        existing = conn.execute(
            "SELECT id FROM alert_config WHERE channel = ?", (channel,)
        ).fetchone()

        if existing:
            conn.execute(
                """UPDATE alert_config SET
                       webhook_url = ?, bot_token = ?, chat_id = ?,
                       enabled = ?,
                       alert_offline = ?, alert_hashrate_drop = ?,
                       alert_negative_profit = ?, alert_daily_summary = ?,
                       hashrate_drop_pct = ?
                   WHERE channel = ?""",
                (
                    webhook_url, bot_token, chat_id,
                    1 if settings.get("enabled", True) else 0,
                    1 if settings.get("alert_offline", True) else 0,
                    1 if settings.get("alert_hashrate_drop", True) else 0,
                    1 if settings.get("alert_negative_profit", True) else 0,
                    1 if settings.get("alert_daily_summary", True) else 0,
                    settings.get("hashrate_drop_pct", 20.0),
                    channel,
                ),
            )
        else:
            conn.execute(
                """INSERT INTO alert_config
                   (channel, webhook_url, bot_token, chat_id, enabled,
                    alert_offline, alert_hashrate_drop, alert_negative_profit,
                    alert_daily_summary, hashrate_drop_pct, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    channel, webhook_url, bot_token, chat_id,
                    1 if settings.get("enabled", True) else 0,
                    1 if settings.get("alert_offline", True) else 0,
                    1 if settings.get("alert_hashrate_drop", True) else 0,
                    1 if settings.get("alert_negative_profit", True) else 0,
                    1 if settings.get("alert_daily_summary", True) else 0,
                    settings.get("hashrate_drop_pct", 20.0),
                    now,
                ),
            )
        conn.commit()
        conn.close()

    def log_alert(self, alert_type, message, channel, miner_id=None):
        """Write an entry to the alert log."""
        conn = self._get_conn()
        now = datetime.now().isoformat()
        conn.execute(
            """INSERT INTO alert_log (alert_type, miner_id, message, sent_at, channel)
               VALUES (?, ?, ?, ?, ?)""",
            (alert_type, miner_id, message, now, channel),
        )
        conn.commit()
        conn.close()

    def get_recent_alerts(self, limit=50):
        """Get the most recent alert log entries."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM alert_log ORDER BY sent_at DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def was_alert_sent_recently(self, alert_type, miner_id=None, hours=1):
        """Check if the same alert was sent within the last N hours (dedup)."""
        conn = self._get_conn()
        since = (datetime.now() - timedelta(hours=hours)).isoformat()
        if miner_id:
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM alert_log
                   WHERE alert_type = ? AND miner_id = ? AND sent_at > ?""",
                (alert_type, miner_id, since),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT COUNT(*) as cnt FROM alert_log
                   WHERE alert_type = ? AND miner_id IS NULL AND sent_at > ?""",
                (alert_type, since),
            ).fetchone()
        conn.close()
        return row["cnt"] > 0

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
