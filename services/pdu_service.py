"""PDU (Power Distribution Unit) service — strike-price auto-pause for miners.

Monitors miner profitability and automatically powers off/on miners via
smart PDUs (Tasmota, TP-Link Kasa, or generic REST) when profit drops
below / recovers above configurable thresholds.
"""

import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "history.db"


class PDUService:
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
            CREATE TABLE IF NOT EXISTS auto_pause_config (
                miner_id TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                pdu_type TEXT NOT NULL DEFAULT 'tasmota',
                pdu_host TEXT NOT NULL DEFAULT '',
                pdu_outlet INTEGER NOT NULL DEFAULT 1,
                pdu_custom_url TEXT DEFAULT '',
                threshold_minutes INTEGER NOT NULL DEFAULT 30,
                resume_threshold REAL NOT NULL DEFAULT 0.0,
                currently_paused INTEGER NOT NULL DEFAULT 0,
                paused_since TEXT,
                unprofitable_since TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS auto_pause_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                miner_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_pause_log_miner
                ON auto_pause_log(miner_id, timestamp);
        """)
        conn.commit()
        conn.close()

    def get_config(self, miner_id: str) -> dict | None:
        """Load auto-pause config for a single miner."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM auto_pause_config WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()
        conn.close()
        if row:
            return dict(row)
        return None

    def get_all_configs(self) -> list[dict]:
        """Load auto-pause configs for all miners."""
        conn = self._get_conn()
        rows = conn.execute("SELECT * FROM auto_pause_config").fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def save_config(self, miner_id: str, config_dict: dict) -> dict:
        """Upsert auto-pause configuration for a miner."""
        conn = self._get_conn()
        now = datetime.now().isoformat()

        existing = conn.execute(
            "SELECT miner_id FROM auto_pause_config WHERE miner_id = ?",
            (miner_id,),
        ).fetchone()

        enabled = int(config_dict.get("enabled", 0))
        pdu_type = config_dict.get("pdu_type", "tasmota")
        pdu_host = config_dict.get("pdu_host", "")
        pdu_outlet = int(config_dict.get("pdu_outlet", 1))
        pdu_custom_url = config_dict.get("pdu_custom_url", "")
        threshold_minutes = int(config_dict.get("threshold_minutes", 30))
        resume_threshold = float(config_dict.get("resume_threshold", 0.0))

        if existing:
            conn.execute(
                """UPDATE auto_pause_config SET
                    enabled=?, pdu_type=?, pdu_host=?, pdu_outlet=?,
                    pdu_custom_url=?, threshold_minutes=?, resume_threshold=?,
                    updated_at=?
                   WHERE miner_id=?""",
                (enabled, pdu_type, pdu_host, pdu_outlet,
                 pdu_custom_url, threshold_minutes, resume_threshold,
                 now, miner_id),
            )
        else:
            conn.execute(
                """INSERT INTO auto_pause_config
                   (miner_id, enabled, pdu_type, pdu_host, pdu_outlet,
                    pdu_custom_url, threshold_minutes, resume_threshold,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (miner_id, enabled, pdu_type, pdu_host, pdu_outlet,
                 pdu_custom_url, threshold_minutes, resume_threshold,
                 now, now),
            )

        conn.commit()
        conn.close()
        return self.get_config(miner_id)

    def power_off(self, config: dict) -> bool:
        """Send HTTP request to PDU to turn off the outlet."""
        return self._send_power_command(config, "off")

    def power_on(self, config: dict) -> bool:
        """Send HTTP request to PDU to turn on the outlet."""
        return self._send_power_command(config, "on")

    def _send_power_command(self, config: dict, action: str) -> bool:
        """Send power command to the PDU based on its type."""
        pdu_type = config.get("pdu_type", "tasmota")
        host = config.get("pdu_host", "")
        outlet = config.get("pdu_outlet", 1)

        if not host:
            logger.error("PDU host not configured for miner %s", config.get("miner_id"))
            return False

        try:
            if pdu_type == "tasmota":
                cmd = "On" if action == "on" else "Off"
                url = f"http://{host}/cm?cmnd=Power{outlet}%20{cmd}"
                resp = requests.get(url, timeout=5)
                if resp.ok:
                    logger.info("Tasmota %s outlet %d: %s", host, outlet, action)
                    return True
                logger.error("Tasmota command failed: %s %s", resp.status_code, resp.text)

            elif pdu_type == "kasa":
                # python-kasa style REST endpoint (kasa-rest-api or similar proxy)
                url = f"http://{host}/api/outlet/{outlet}"
                payload = {"state": action == "on"}
                resp = requests.post(url, json=payload, timeout=5)
                if resp.ok:
                    logger.info("Kasa %s outlet %d: %s", host, outlet, action)
                    return True
                logger.error("Kasa command failed: %s %s", resp.status_code, resp.text)

            elif pdu_type == "generic":
                # Custom URL with {action} and {outlet} placeholders
                custom_url = config.get("pdu_custom_url", "")
                if not custom_url:
                    logger.error("Generic PDU URL not configured for miner %s", config.get("miner_id"))
                    return False
                url = custom_url.replace("{action}", action).replace("{outlet}", str(outlet))
                resp = requests.get(url, timeout=5)
                if resp.ok:
                    logger.info("Generic PDU %s: %s", url, action)
                    return True
                logger.error("Generic PDU command failed: %s %s", resp.status_code, resp.text)

            else:
                logger.error("Unknown PDU type: %s", pdu_type)

        except requests.RequestException as e:
            logger.error("PDU communication error (%s %s): %s", pdu_type, host, e)

        return False

    def check_and_autopause(self, miner_results: list) -> list[dict]:
        """Check all miners with auto-pause enabled and take action.

        Returns a list of actions taken: [{"miner_id": ..., "action": "paused"|"resumed", ...}]
        """
        actions_taken = []
        configs = self.get_all_configs()
        if not configs:
            return actions_taken

        # Build lookup: miner_id -> profitability result
        profit_lookup = {}
        for r in miner_results:
            miner = r.get("miner", {})
            profit_lookup[miner.get("id")] = r

        conn = self._get_conn()
        now = datetime.now()

        for cfg in configs:
            if not cfg.get("enabled"):
                continue

            miner_id = cfg["miner_id"]
            result = profit_lookup.get(miner_id)
            if not result:
                continue

            daily_profit = result.get("best_daily_profit", 0)
            currently_paused = cfg.get("currently_paused", 0)
            threshold_minutes = cfg.get("threshold_minutes", 30)
            resume_threshold = cfg.get("resume_threshold", 0.0)

            if daily_profit < 0 and not currently_paused:
                # Miner is unprofitable — check if it's been long enough
                unprofitable_since = cfg.get("unprofitable_since")
                if not unprofitable_since:
                    # Just started being unprofitable — mark the time
                    conn.execute(
                        "UPDATE auto_pause_config SET unprofitable_since=? WHERE miner_id=?",
                        (now.isoformat(), miner_id),
                    )
                else:
                    try:
                        since = datetime.fromisoformat(unprofitable_since)
                        elapsed_minutes = (now - since).total_seconds() / 60
                        if elapsed_minutes >= threshold_minutes:
                            # Threshold exceeded — power off
                            success = self.power_off(cfg)
                            if success:
                                conn.execute(
                                    """UPDATE auto_pause_config SET
                                        currently_paused=1, paused_since=?,
                                        unprofitable_since=NULL, updated_at=?
                                       WHERE miner_id=?""",
                                    (now.isoformat(), now.isoformat(), miner_id),
                                )
                                conn.execute(
                                    "INSERT INTO auto_pause_log (miner_id, action, reason) VALUES (?, ?, ?)",
                                    (miner_id, "paused",
                                     f"Profit ${daily_profit:.2f}/day for {elapsed_minutes:.0f}min"),
                                )
                                actions_taken.append({
                                    "miner_id": miner_id,
                                    "action": "paused",
                                    "profit": daily_profit,
                                    "elapsed_minutes": round(elapsed_minutes),
                                })
                                logger.info(
                                    "Auto-paused miner %s: profit $%.2f/day for %d min",
                                    miner_id, daily_profit, elapsed_minutes,
                                )
                    except (ValueError, TypeError) as e:
                        logger.error("Bad unprofitable_since for %s: %s", miner_id, e)

            elif daily_profit >= 0 and not currently_paused:
                # Profitable again — clear unprofitable timer
                if cfg.get("unprofitable_since"):
                    conn.execute(
                        "UPDATE auto_pause_config SET unprofitable_since=NULL WHERE miner_id=?",
                        (miner_id,),
                    )

            elif daily_profit > resume_threshold and currently_paused:
                # Miner is paused but now profitable above resume threshold — power on
                success = self.power_on(cfg)
                if success:
                    conn.execute(
                        """UPDATE auto_pause_config SET
                            currently_paused=0, paused_since=NULL, updated_at=?
                           WHERE miner_id=?""",
                        (now.isoformat(), miner_id),
                    )
                    conn.execute(
                        "INSERT INTO auto_pause_log (miner_id, action, reason) VALUES (?, ?, ?)",
                        (miner_id, "resumed",
                         f"Profit ${daily_profit:.2f}/day > resume threshold ${resume_threshold:.2f}"),
                    )
                    actions_taken.append({
                        "miner_id": miner_id,
                        "action": "resumed",
                        "profit": daily_profit,
                    })
                    logger.info(
                        "Auto-resumed miner %s: profit $%.2f/day > threshold $%.2f",
                        miner_id, daily_profit, resume_threshold,
                    )

        conn.commit()
        conn.close()
        return actions_taken

    def get_pause_status(self) -> list[dict]:
        """Return all miners with their auto-pause state and recent log."""
        conn = self._get_conn()
        configs = conn.execute("SELECT * FROM auto_pause_config").fetchall()

        result = []
        for cfg in configs:
            miner_id = cfg["miner_id"]
            # Get last 5 log entries
            logs = conn.execute(
                "SELECT action, reason, timestamp FROM auto_pause_log WHERE miner_id=? ORDER BY timestamp DESC LIMIT 5",
                (miner_id,),
            ).fetchall()

            result.append({
                "miner_id": miner_id,
                "enabled": bool(cfg["enabled"]),
                "pdu_type": cfg["pdu_type"],
                "pdu_host": cfg["pdu_host"],
                "pdu_outlet": cfg["pdu_outlet"],
                "currently_paused": bool(cfg["currently_paused"]),
                "paused_since": cfg["paused_since"],
                "unprofitable_since": cfg["unprofitable_since"],
                "threshold_minutes": cfg["threshold_minutes"],
                "resume_threshold": cfg["resume_threshold"],
                "recent_log": [dict(l) for l in logs],
            })

        conn.close()
        return result

    def get_log(self, miner_id: str = None, limit: int = 50) -> list[dict]:
        """Get auto-pause action log, optionally filtered by miner."""
        conn = self._get_conn()
        if miner_id:
            rows = conn.execute(
                "SELECT * FROM auto_pause_log WHERE miner_id=? ORDER BY timestamp DESC LIMIT ?",
                (miner_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM auto_pause_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
