import hashlib
import hmac
import logging
import time

import requests

from services.cache_manager import CacheManager

logger = logging.getLogger(__name__)


class CoinbaseService:
    """Fetches wallet balances from the Coinbase v2 API (legacy API key auth)."""

    BASE_URL = "https://api.coinbase.com"
    API_VERSION = "2018-02-01"
    CACHE_TTL = 300  # 5 minutes

    def __init__(self, api_key: str, api_secret: str, cache: CacheManager):
        self.api_key = api_key
        self.api_secret = api_secret
        self.cache = cache

    def is_configured(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def _sign(self, method: str, path: str, body: str = "") -> dict:
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
            "CB-VERSION": self.API_VERSION,
            "Content-Type": "application/json",
        }

    def _get(self, path: str) -> dict | None:
        try:
            headers = self._sign("GET", path)
            resp = requests.get(
                self.BASE_URL + path, headers=headers, timeout=10
            )
            if resp.ok:
                return resp.json()
            logger.error("Coinbase API %s: %s %s", path, resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Coinbase API error: %s", e)
        return None

    def get_accounts(self) -> list[dict]:
        """Fetch all Coinbase accounts with balances. Uses cache."""
        cached = self.cache.get("accounts", self.CACHE_TTL)
        if cached is not None:
            return cached

        accounts = []
        path = "/v2/accounts?limit=100"

        while path:
            data = self._get(path)
            if not data:
                break
            for acct in data.get("data", []):
                balance = float(acct.get("balance", {}).get("amount", 0))
                if balance == 0:
                    continue
                accounts.append({
                    "id": acct.get("id"),
                    "name": acct.get("name", ""),
                    "currency": acct.get("currency", {}).get("code", ""),
                    "currency_name": acct.get("currency", {}).get("name", ""),
                    "balance": balance,
                    "native_balance": float(
                        acct.get("native_balance", {}).get("amount", 0)
                    ),
                    "native_currency": acct.get("native_balance", {}).get(
                        "currency", "USD"
                    ),
                    "type": acct.get("type", "wallet"),
                })
            # Pagination
            pagination = data.get("pagination", {})
            next_uri = pagination.get("next_uri")
            path = next_uri if next_uri else None

        self.cache.set("accounts", accounts)
        return accounts

    def get_portfolio_summary(self) -> dict:
        """Return portfolio summary: total value, holdings sorted by value."""
        accounts = self.get_accounts()
        total_usd = sum(a["native_balance"] for a in accounts)
        holdings = sorted(accounts, key=lambda a: a["native_balance"], reverse=True)
        return {
            "total_usd": round(total_usd, 2),
            "holdings": holdings,
            "count": len(holdings),
        }

    def get_cache_age(self) -> int | None:
        return self.cache.get_age_seconds("accounts")
