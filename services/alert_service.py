"""Telegram / Discord alert service for mining events."""

import logging
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class AlertService:
    def __init__(self, history_svc):
        self.history_svc = history_svc
        self._configs = []
        self.reload_configs()

    def reload_configs(self):
        """Load alert configurations from DB."""
        try:
            self._configs = self.history_svc.get_alert_configs()
        except Exception as e:
            logger.error("Failed to load alert configs: %s", e)
            self._configs = []

    # ---- Low-level senders ----

    def send_telegram(self, bot_token, chat_id, message):
        """Send a message via Telegram Bot API (MarkdownV2-safe fallback to HTML)."""
        url = TELEGRAM_API.format(token=bot_token)
        try:
            resp = requests.post(url, json={
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }, timeout=10)
            if not resp.ok:
                logger.error("Telegram send failed (%s): %s", resp.status_code, resp.text)
                return False
            return True
        except Exception as e:
            logger.error("Telegram send error: %s", e)
            return False

    def send_discord(self, webhook_url, message, title=None, color=None):
        """Send a message via Discord webhook with embed."""
        if color is None:
            color = 0x6366F1  # default purple matching dashboard theme
        embed = {
            "description": message,
            "color": color,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if title:
            embed["title"] = title
        try:
            resp = requests.post(webhook_url, json={
                "embeds": [embed],
            }, timeout=10)
            if not resp.ok:
                logger.error("Discord send failed (%s): %s", resp.status_code, resp.text)
                return False
            return True
        except Exception as e:
            logger.error("Discord send error: %s", e)
            return False

    # ---- Unified send ----

    def send_alert(self, alert_type, message, miner_id=None):
        """Route an alert to all enabled channels. Deduplicates within 1 hour."""
        # Dedup check
        if self.history_svc.was_alert_sent_recently(alert_type, miner_id, hours=1):
            logger.debug("Alert dedup: %s / %s already sent within 1h", alert_type, miner_id)
            return False

        self.reload_configs()
        sent_any = False

        for cfg in self._configs:
            if not cfg.get("enabled"):
                continue

            # Check if this alert type is enabled for this config
            type_map = {
                "offline": "alert_offline",
                "hashrate_drop": "alert_hashrate_drop",
                "negative_profit": "alert_negative_profit",
                "daily_summary": "alert_daily_summary",
            }
            config_key = type_map.get(alert_type)
            if config_key and not cfg.get(config_key):
                continue

            channel = cfg["channel"]
            success = False

            if channel == "telegram":
                bot_token = cfg.get("bot_token", "")
                chat_id = cfg.get("chat_id", "")
                if bot_token and chat_id:
                    success = self.send_telegram(bot_token, chat_id, message)

            elif channel == "discord":
                webhook_url = cfg.get("webhook_url", "")
                if webhook_url:
                    # Pick color based on alert type
                    color_map = {
                        "offline": 0xEF4444,        # red
                        "hashrate_drop": 0xEAB308,   # yellow
                        "negative_profit": 0xEF4444,  # red
                        "daily_summary": 0x6366F1,    # purple
                        "test": 0x22C55E,             # green
                    }
                    color = color_map.get(alert_type, 0x6366F1)
                    title_map = {
                        "offline": "Miner Offline",
                        "hashrate_drop": "Hashrate Drop",
                        "negative_profit": "Negative Profit",
                        "daily_summary": "Daily Mining Summary",
                        "test": "Test Alert",
                    }
                    title = title_map.get(alert_type, "Mining Alert")
                    success = self.send_discord(webhook_url, message, title=title, color=color)

            if success:
                self.history_svc.log_alert(alert_type, message, channel, miner_id)
                sent_any = True
                logger.info("Alert sent [%s] via %s: %s", alert_type, channel,
                            message[:80])

        return sent_any

    # ---- Check functions (called from background thread) ----

    def check_miner_offline(self, pool_statuses, miners):
        """Alert for any miner that is offline according to PowerPool."""
        for miner in miners:
            if miner.get("status") == "inactive":
                continue
            miner_id = miner["id"]
            status = pool_statuses.get(miner_id)
            if status is not None and not status.get("online", True):
                msg = (
                    f"\u26a0\ufe0f <b>Miner Offline</b>\n"
                    f"<b>{miner.get('name', miner_id)}</b> "
                    f"({miner.get('model', 'Unknown')}) is not responding.\n"
                    f"Location: {miner.get('location_id', 'N/A')}"
                )
                self.send_alert("offline", msg, miner_id=miner_id)

    @staticmethod
    def _normalize_hashrate(value, unit):
        """Normalize hashrate to H/s for comparison."""
        multipliers = {
            "H/s": 1, "Sol/s": 1,
            "KH/s": 1e3, "KSol/s": 1e3,
            "MH/s": 1e6,
            "GH/s": 1e9,
            "TH/s": 1e12,
            "PH/s": 1e15,
        }
        return value * multipliers.get(unit, 1)

    def check_hashrate_drop(self, pool_statuses, miners):
        """Alert when current hashrate drops below configured % of rated hashrate."""
        self.reload_configs()
        # Find the hashrate drop threshold from any enabled config
        threshold_pct = 20.0
        for cfg in self._configs:
            if cfg.get("enabled") and cfg.get("alert_hashrate_drop"):
                threshold_pct = cfg.get("hashrate_drop_pct", 20.0)
                break

        for miner in miners:
            if miner.get("status") == "inactive":
                continue
            miner_id = miner["id"]
            status = pool_statuses.get(miner_id)
            if status is None or not status.get("online", False):
                continue

            current_hr = status.get("hashrate", 0)
            rated_hr = miner.get("hashrate", 0)
            if rated_hr <= 0 or current_hr <= 0:
                continue

            # Normalize both to H/s for accurate comparison
            pool_unit = status.get("hashrate_units", "") or status.get("hashrate_avg_units", "")
            miner_unit = miner.get("hashrate_unit", "")
            current_normalized = self._normalize_hashrate(current_hr, pool_unit)
            rated_normalized = self._normalize_hashrate(rated_hr, miner_unit)

            if rated_normalized <= 0:
                continue

            drop_pct = ((rated_normalized - current_normalized) / rated_normalized) * 100
            if drop_pct >= threshold_pct:
                msg = (
                    f"\u26a0\ufe0f <b>Hashrate Drop</b>\n"
                    f"<b>{miner.get('name', miner_id)}</b>: "
                    f"{current_hr:.1f} {pool_unit} "
                    f"(rated {rated_hr:.1f} {miner_unit})\n"
                    f"Drop: <b>{drop_pct:.1f}%</b> (threshold: {threshold_pct}%)"
                )
                self.send_alert("hashrate_drop", msg, miner_id=miner_id)

    def check_negative_profit(self, miner_results):
        """Alert when any miner has daily_profit < 0."""
        for r in miner_results:
            m = r.get("miner", {})
            if m.get("status") == "inactive":
                continue
            profit = r.get("best_daily_profit", 0)
            if profit < 0:
                msg = (
                    f"\ud83d\udcc9 <b>Negative Profit</b>\n"
                    f"<b>{m.get('name', m.get('id', '?'))}</b> "
                    f"is losing <b>${abs(profit):.2f}/day</b>\n"
                    f"Revenue: ${r.get('daily_revenue', 0):.2f} | "
                    f"Electricity: ${r.get('daily_electricity', 0):.2f}"
                )
                self.send_alert("negative_profit", msg, miner_id=m.get("id"))

    def send_daily_summary(self, summary_data):
        """Send a formatted daily P&L summary."""
        miners = summary_data.get("miners", [])
        total_revenue = sum(r.get("daily_revenue", 0) for r in miners)
        total_electricity = sum(r.get("daily_electricity", 0) for r in miners)
        total_profit = sum(r.get("best_daily_profit", 0) for r in miners)
        active_count = sum(1 for r in miners
                          if r.get("miner", {}).get("status") != "inactive")
        profitable_count = sum(1 for r in miners
                               if r.get("best_daily_profit", 0) > 0)

        profit_emoji = "\u2705" if total_profit >= 0 else "\ud83d\udd34"

        msg = (
            f"\ud83d\udcca <b>Daily Mining Summary</b>\n\n"
            f"Active miners: {active_count} "
            f"({profitable_count} profitable)\n"
            f"Revenue:    <b>${total_revenue:.2f}</b>\n"
            f"Electricity: <b>${total_electricity:.2f}</b>\n"
            f"{profit_emoji} Profit:     <b>${total_profit:.2f}</b>\n"
            f"Monthly est: <b>${total_profit * 30:.2f}</b>"
        )

        # Top 3 performers
        sorted_miners = sorted(miners,
                               key=lambda r: r.get("best_daily_profit", 0),
                               reverse=True)
        if sorted_miners:
            msg += "\n\n<b>Top performers:</b>"
            for r in sorted_miners[:3]:
                m = r.get("miner", {})
                p = r.get("best_daily_profit", 0)
                msg += f"\n  {m.get('name', '?')}: ${p:.2f}/day"

        self.send_alert("daily_summary", msg)

    # ---- Config CRUD ----

    def get_config(self):
        """Return all alert configs as list of dicts."""
        self.reload_configs()
        return self._configs

    def save_config(self, channel, webhook_url=None, bot_token=None,
                    chat_id=None, settings=None):
        """Save/update alert config for a channel."""
        self.history_svc.save_alert_config(
            channel, webhook_url=webhook_url, bot_token=bot_token,
            chat_id=chat_id, settings=settings,
        )
        self.reload_configs()

    def get_recent_alerts(self, limit=50):
        """Return recent alert log entries."""
        return self.history_svc.get_recent_alerts(limit=limit)
