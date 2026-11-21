"""Daily USD price per coin, kept forever, for valuing income on the day it was received.

Source: CoinGecko public API, /coins/{id}/market_chart?days=N&interval=daily (no key; the
public tier serves up to 365 days back, so the first run backfills a year and later runs
top up the last week). One row per (UTC day, coin); a later fetch for the same day wins.
Checked 2026-11-21: CoinGecko "pearl-2" is Pearl (PRL, $1.86 vs Kryptex $1.85).
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from fleet import store

logger = logging.getLogger(__name__)

API = "https://api.coingecko.com/api/v3"
COINS = {
    "BTC": "bitcoin",
    "ZEC": "zcash",
    "LTC": "litecoin",
    "DOGE": "dogecoin",
    "CLORE": "clore-ai",
    "PRL": "pearl-2",
    "QTC": "qubitcoin-2",
}
BACKFILL_DAYS = 365
TOPUP_DAYS = 7
PAUSE_S = 15  # public tier allows only a few calls a minute

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_daily (
    day TEXT NOT NULL,      -- YYYY-MM-DD (UTC)
    coin TEXT NOT NULL,     -- ticker, e.g. BTC
    usd REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (day, coin)
) WITHOUT ROWID;
"""


def parse_chart(prices) -> dict:
    """[[ms, usd], ...] -> {"YYYY-MM-DD": usd}; the last point of a day wins."""
    out = {}
    for point in prices or []:
        try:
            ms, usd = point[0], float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        out[day] = usd
    return out


def _last_day(conn, coin) -> str | None:
    row = conn.execute("SELECT MAX(day) FROM price_daily WHERE coin=?", (coin,)).fetchone()
    return row[0] if row else None


def save(coin: str, by_day: dict, source: str = "coingecko"):
    with store.connect() as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT OR REPLACE INTO price_daily (day, coin, usd, source) VALUES (?, ?, ?, ?)",
                         [(d, coin, usd, source) for d, usd in by_day.items()])


def tick(pause_s: float = PAUSE_S) -> int:
    """Backfill or top up each coin. Returns the number of coins updated. Never raises."""
    with store.connect() as conn:
        conn.executescript(SCHEMA)
        last = {coin: _last_day(conn, coin) for coin in COINS}
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    updated = 0
    for coin, cg_id in COINS.items():
        if last[coin] and last[coin] >= yesterday:
            continue
        days = TOPUP_DAYS if last[coin] else BACKFILL_DAYS
        try:
            resp = requests.get(f"{API}/coins/{cg_id}/market_chart",
                                params={"vs_currency": "usd", "days": days, "interval": "daily"},
                                timeout=20)
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            by_day = parse_chart(resp.json().get("prices"))
            if by_day:
                save(coin, by_day)
                updated += 1
        except Exception as e:
            logger.warning("Price history for %s failed: %s", coin, e)
        time.sleep(pause_s)
    return updated


def price_on(coin: str, day: str) -> float | None:
    """USD price for a coin on a UTC day, or the nearest earlier day within a week."""
    with store.connect(readonly=True) as conn:
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT usd FROM price_daily WHERE coin=? AND day<=? AND day>=? "
                               "ORDER BY day DESC LIMIT 1",
                               (coin.upper(), day,
                                (datetime.fromisoformat(day) - timedelta(days=7)).strftime("%Y-%m-%d"))
                               ).fetchone()
        except Exception:
            return None
    return row[0] if row else None
