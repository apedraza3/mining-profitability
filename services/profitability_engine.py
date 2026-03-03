import json
import logging
from datetime import datetime, timedelta

from services.whattomine_service import WhatToMineService
from services.hashrateno_service import HashrateNoService
from services.miningnow_service import MiningNowService
from services.inventory_manager import InventoryManager
import config

logger = logging.getLogger(__name__)


class ProfitabilityEngine:
    def __init__(
        self,
        whattomine: WhatToMineService,
        hashrateno: HashrateNoService,
        miningnow: MiningNowService,
        inventory_mgr: InventoryManager,
    ):
        self.whattomine = whattomine
        self.hashrateno = hashrateno
        self.miningnow = miningnow
        self.inventory = inventory_mgr
        self.coin_mappings = self._load_coin_mappings()

    def _load_coin_mappings(self) -> dict:
        try:
            with open(config.COIN_MAPPINGS_FILE, "r") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    @staticmethod
    def daily_electricity_cost(wattage: int, cost_kwh: float) -> float:
        return (wattage * 24 / 1000) * cost_kwh

    @staticmethod
    def calculate_roi(
        purchase_price: float,
        quantity: int,
        daily_profit: float,
        purchase_date: str,
    ) -> dict:
        total_investment = purchase_price * quantity
        if daily_profit <= 0:
            return {
                "total_investment": total_investment,
                "best_daily_profit": daily_profit,
                "best_monthly_profit": daily_profit * 30,
                "days_to_roi": -1,
                "estimated_payback_date": None,
                "roi_percentage_30d": (
                    (daily_profit * 30 / total_investment * 100)
                    if total_investment > 0
                    else 0
                ),
            }

        total_daily_profit = daily_profit * quantity
        days_to_roi = (
            int(total_investment / total_daily_profit)
            if total_daily_profit > 0
            else -1
        )

        payback_date = None
        if days_to_roi > 0 and purchase_date:
            try:
                start = datetime.strptime(purchase_date, "%Y-%m-%d")
                payback_date = (start + timedelta(days=days_to_roi)).strftime("%Y-%m-%d")
            except ValueError:
                pass

        return {
            "total_investment": total_investment,
            "best_daily_profit": daily_profit,
            "best_monthly_profit": daily_profit * 30,
            "days_to_roi": days_to_roi,
            "estimated_payback_date": payback_date,
            "roi_percentage_30d": (
                (total_daily_profit * 30 / total_investment * 100)
                if total_investment > 0
                else 0
            ),
        }

    def _get_whattomine_data(self, miner: dict, location: dict) -> dict:
        """Get WhatToMine profitability for a miner."""
        result = {
            "available": False,
            "best_coin": None,
            "daily_revenue": 0,
            "daily_electricity": 0,
            "daily_profit": 0,
            "monthly_profit": 0,
            "all_coins": [],
        }
        try:
            coins = self.whattomine.get_profitability_for_miner(
                miner, location, self.coin_mappings
            )
            if coins:
                best = coins[0]
                result["available"] = True
                result["best_coin"] = best["tag"]
                result["daily_revenue"] = best["daily_revenue"]
                result["daily_electricity"] = best["daily_electricity"]
                result["daily_profit"] = best["daily_profit"]
                result["monthly_profit"] = best["daily_profit"] * 30
                result["all_coins"] = coins
        except Exception as e:
            logger.error("WhatToMine error for %s: %s", miner.get("name"), e)
        return result

    def _get_hashrateno_data(self, miner: dict, location: dict) -> dict:
        """Get Hashrate.no profitability for a miner."""
        result = {
            "available": False,
            "daily_revenue": 0,
            "daily_electricity": 0,
            "daily_profit": 0,
            "monthly_profit": 0,
            "matched_model": None,
            "match_confidence": 0,
        }
        if not self.hashrateno.is_configured():
            return result

        try:
            model_key = miner.get("hashrateno_model_key") or miner.get("model", "")
            match = self.hashrateno.find_model_estimate(
                model_key, miner.get("type", "ASIC")
            )
            if match:
                raw = match["raw_data"]
                # With powerCost=0, the "profit" or "revenue" field is pure revenue
                # We need to extract revenue and calculate electricity ourselves
                daily_rev = self._extract_revenue(raw)
                daily_elec = self.daily_electricity_cost(
                    miner["wattage"], location["electricity_cost_kwh"]
                )
                daily_profit = daily_rev - daily_elec

                result["available"] = True
                result["daily_revenue"] = daily_rev
                result["daily_electricity"] = daily_elec
                result["daily_profit"] = daily_profit
                result["monthly_profit"] = daily_profit * 30
                result["matched_model"] = match["matched_name"]
                result["match_confidence"] = match["match_confidence"]
        except Exception as e:
            logger.error("Hashrate.no error for %s: %s", miner.get("name"), e)
        return result

    def _extract_revenue(self, raw_data: dict) -> float:
        """Extract daily revenue from Hashrate.no raw data.
        The API response structure may vary — try common field names."""
        # Try direct revenue/profit fields
        for key in ["revenue", "profit", "dailyRevenue", "dailyProfit",
                     "estimatedRevenue", "daily_revenue"]:
            val = raw_data.get(key)
            if val is not None:
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue

        # Try nested coins array — sum up revenues
        coins = raw_data.get("coins", raw_data.get("estimatedCoins", []))
        if isinstance(coins, list):
            total = 0
            for coin in coins:
                for key in ["revenue", "estimatedRevenue", "profit", "usd"]:
                    val = coin.get(key)
                    if val is not None:
                        try:
                            total += float(val)
                            break
                        except (ValueError, TypeError):
                            continue
            if total > 0:
                return total

        return 0.0

    def _get_miningnow_data(self, miner: dict) -> dict:
        """Get MiningNow data for a miner (ASIC only)."""
        result = {
            "available": False,
            "rank": None,
            "profitability_score": None,
            "best_price": None,
            "matched_model": None,
            "match_confidence": 0,
        }
        if miner.get("type") != "ASIC":
            return result

        try:
            model_key = miner.get("miningnow_model_key") or miner.get("model", "")
            match = self.miningnow.find_miner_data(model_key)
            if match:
                result["available"] = True
                result["rank"] = match.get("rank")
                result["profitability_score"] = match.get("profitability_score")
                result["best_price"] = match.get("best_price")
                result["matched_model"] = match.get("name")
                result["match_confidence"] = match.get("match_confidence", 0)
        except Exception as e:
            logger.error("MiningNow error for %s: %s", miner.get("name"), e)
        return result

    def _get_status(self, daily_profit: float) -> str:
        if daily_profit >= config.PROFITABLE_THRESHOLD:
            return "profitable"
        elif daily_profit >= config.MARGINAL_THRESHOLD:
            return "marginal"
        else:
            return "unprofitable"

    def calculate_for_miner(self, miner: dict, location: dict) -> dict:
        """Calculate full profitability data for one miner."""
        if miner.get("status") == "inactive":
            return {
                "miner": miner,
                "location": location,
                "sources": {
                    "whattomine": {"available": False},
                    "hashrateno": {"available": False},
                    "miningnow": {"available": False},
                },
                "roi": self.calculate_roi(
                    miner.get("purchase_price", 0),
                    miner.get("quantity", 1),
                    0,
                    miner.get("purchase_date", ""),
                ),
                "status": "inactive",
            }

        wtm = self._get_whattomine_data(miner, location)
        hrn = self._get_hashrateno_data(miner, location)
        mn = self._get_miningnow_data(miner)

        # Best daily profit from available sources
        profits = []
        if wtm["available"]:
            profits.append(wtm["daily_profit"])
        if hrn["available"]:
            profits.append(hrn["daily_profit"])
        best_daily = max(profits) if profits else 0

        roi = self.calculate_roi(
            miner.get("purchase_price", 0),
            miner.get("quantity", 1),
            best_daily,
            miner.get("purchase_date", ""),
        )

        return {
            "miner": miner,
            "location": location,
            "sources": {
                "whattomine": wtm,
                "hashrateno": hrn,
                "miningnow": mn,
            },
            "roi": roi,
            "best_daily_profit": best_daily,
            "status": self._get_status(best_daily),
        }

    def calculate_all(self) -> dict:
        """Calculate profitability for all miners in inventory."""
        miners = self.inventory.get_all_miners()
        locations = {l["id"]: l for l in self.inventory.get_all_locations()}

        results = []
        for miner in miners:
            loc_id = miner.get("location_id", "")
            location = locations.get(loc_id, {
                "id": loc_id,
                "name": loc_id or "Unknown",
                "electricity_cost_kwh": 0.10,
                "currency": "USD",
            })
            result = self.calculate_for_miner(miner, location)
            results.append(result)

        # Summary
        active = [r for r in results if r["status"] != "inactive"]
        total_daily_revenue = sum(
            max(
                r["sources"]["whattomine"].get("daily_revenue", 0),
                r["sources"]["hashrateno"].get("daily_revenue", 0),
            ) * r["miner"].get("quantity", 1)
            for r in active
        )
        total_daily_elec = sum(
            self.daily_electricity_cost(
                r["miner"]["wattage"], r["location"].get("electricity_cost_kwh", 0.10)
            ) * r["miner"].get("quantity", 1)
            for r in active
        )
        total_daily_profit = total_daily_revenue - total_daily_elec
        total_investment = sum(
            r["miner"].get("purchase_price", 0) * r["miner"].get("quantity", 1)
            for r in results
        )

        profitable_count = sum(1 for r in active if r["status"] == "profitable")
        unprofitable_count = sum(1 for r in active if r["status"] == "unprofitable")
        marginal_count = sum(1 for r in active if r["status"] == "marginal")

        # By location
        by_location = {}
        for r in active:
            loc_name = r["location"].get("name", "Unknown")
            if loc_name not in by_location:
                by_location[loc_name] = {
                    "electricity_cost_kwh": r["location"].get("electricity_cost_kwh", 0),
                    "miners": 0,
                    "units": 0,
                    "daily_profit": 0,
                }
            by_location[loc_name]["miners"] += 1
            by_location[loc_name]["units"] += r["miner"].get("quantity", 1)
            by_location[loc_name]["daily_profit"] += (
                r.get("best_daily_profit", 0) * r["miner"].get("quantity", 1)
            )

        # Cache status
        cache_status = {
            "whattomine_age": self._format_age(
                self.whattomine.cache.get_age_seconds("coins_index")
            ),
            "hashrateno_age": self._format_age(self.hashrateno.get_cache_age()),
            "miningnow_age": self._format_age(
                self.miningnow.cache.get_age_seconds("miner_list")
            ),
        }

        return {
            "miners": results,
            "summary": {
                "total_miners": len(miners),
                "total_units": sum(m.get("quantity", 1) for m in miners),
                "profitable_count": profitable_count,
                "unprofitable_count": unprofitable_count,
                "marginal_count": marginal_count,
                "total_daily_revenue": round(total_daily_revenue, 2),
                "total_daily_electricity": round(total_daily_elec, 2),
                "total_daily_profit": round(total_daily_profit, 2),
                "total_monthly_profit": round(total_daily_profit * 30, 2),
                "total_investment": round(total_investment, 2),
                "portfolio_roi_days": (
                    int(total_investment / total_daily_profit)
                    if total_daily_profit > 0
                    else -1
                ),
            },
            "by_location": by_location,
            "last_updated": datetime.now().isoformat(),
            "cache_status": cache_status,
        }

    @staticmethod
    def _format_age(seconds: int | None) -> str:
        if seconds is None:
            return "No data"
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
