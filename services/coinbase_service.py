import logging
import secrets
import time

import jwt
import requests

from services.cache_manager import CacheManager

logger = logging.getLogger(__name__)


class CoinbaseService:
    """Fetches wallet balances from the Coinbase API using CDP API key (JWT/ES256)."""

    BASE_URL = "https://api.coinbase.com"
    API_VERSION = "2018-02-01"
    CACHE_TTL = 300  # 5 minutes

    def __init__(self, api_key_name: str, api_private_key: str, cache: CacheManager):
        self.api_key_name = api_key_name
        self.api_private_key = api_private_key.replace("\\n", "\n")
        self.cache = cache

    def is_configured(self) -> bool:
        return bool(self.api_key_name and self.api_private_key)

    def _build_jwt(self, method: str, path: str) -> str:
        """Build a signed JWT for CDP API key authentication."""
        uri = f"{method.upper()} api.coinbase.com{path}"
        now = int(time.time())
        payload = {
            "sub": self.api_key_name,
            "iss": "cdp",
            "aud": ["cdp_service"],
            "nbf": now,
            "exp": now + 120,
            "uris": [uri],
        }
        headers = {
            "kid": self.api_key_name,
            "nonce": secrets.token_hex(16),
            "typ": "JWT",
        }
        return jwt.encode(payload, self.api_private_key, algorithm="ES256", headers=headers)

    def _get(self, path: str) -> dict | None:
        try:
            token = self._build_jwt("GET", path.split("?")[0])
            headers = {
                "Authorization": f"Bearer {token}",
                "CB-VERSION": self.API_VERSION,
                "Content-Type": "application/json",
            }
            resp = requests.get(
                self.BASE_URL + path, headers=headers, timeout=10
            )
            if resp.ok:
                return resp.json()
            logger.error("Coinbase API %s: %s %s", path, resp.status_code, resp.text[:200])
        except Exception as e:
            logger.error("Coinbase API error: %s", e)
        return None

    def _get_usd_prices(self, currencies: list[str]) -> dict[str, float]:
        """Fetch USD prices for given currency codes via Coinbase exchange rates (public, no auth)."""
        prices = {}
        for code in currencies:
            try:
                resp = requests.get(
                    f"{self.BASE_URL}/v2/exchange-rates?currency={code}",
                    timeout=10,
                )
                if resp.ok:
                    rate = resp.json().get("data", {}).get("rates", {}).get("USD")
                    if rate:
                        prices[code] = float(rate)
            except Exception as e:
                logger.warning("Failed to fetch price for %s: %s", code, e)
        return prices

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

        # If native_balance is 0 for all accounts, fetch prices and calculate USD values
        needs_prices = accounts and all(a["native_balance"] == 0 for a in accounts)
        logger.info("Coinbase accounts: %d found, needs_prices=%s", len(accounts), needs_prices)
        if needs_prices:
            currencies = list({a["currency"] for a in accounts})
            logger.info("Fetching USD prices for: %s", currencies)
            prices = self._get_usd_prices(currencies)
            logger.info("Got prices: %s", prices)
            for acct in accounts:
                price = prices.get(acct["currency"], 0)
                acct["native_balance"] = round(acct["balance"] * price, 2)
                acct["native_currency"] = "USD"

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
