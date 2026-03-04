import json
import logging
from datetime import datetime, timedelta

from services.whattomine_service import WhatToMineService
from services.hashrateno_service import HashrateNoService
from services.miningnow_service import MiningNowService
from services.inventory_manager import InventoryManager
from services.power_import import get_miner_actual_watts
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

    def _get_whattomine_data(
        self, miner: dict, location: dict, primary_only: bool = True
    ) -> dict:
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
                miner, location, self.coin_mappings, primary_only=primary_only
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
                daily_rev = match.get("daily_revenue", 0)

                # Scale revenue to user's actual hashrate
                slug = match.get("matched_slug", "")
                ref_spec = config.HASHRATENO_REFERENCE_SPECS.get(slug)
                if ref_spec and miner.get("hashrate"):
                    ref_hr, ref_unit = ref_spec
                    user_hr = miner["hashrate"]
                    # Only scale if same unit (GH/s vs GH/s, TH/s vs TH/s)
                    if ref_unit == miner.get("hashrate_unit") and ref_hr > 0:
                        daily_rev = daily_rev * (user_hr / ref_hr)

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

    def calculate_for_miner(
        self, miner: dict, location: dict, primary_only: bool = True
    ) -> dict:
        """Calculate profitability data for one miner.
        primary_only=True fetches only the main coin per algo (faster, for table view).
        primary_only=False fetches all coins (for detail panel)."""
        # Check for imported actual wattage data (CSV import)
        actual_watts = get_miner_actual_watts(miner.get("name", ""))
        nameplate_watts = miner.get("wattage", 0)
        effective_watts = actual_watts if actual_watts else nameplate_watts

        power_info = {
            "nameplate_watts": nameplate_watts,
            "actual_watts": actual_watts,
            "effective_watts": effective_watts,
            "source": "csv_import" if actual_watts else "nameplate",
        }

        is_inactive = miner.get("status") == "inactive"

        # Use effective watts for Hashrate.no electricity calc
        # WhatToMine calculates its own electricity from the wattage we send,
        # so we recalculate WTM profit using actual watts if available
        wtm = self._get_whattomine_data(miner, location, primary_only=primary_only)
        if actual_watts and wtm["available"]:
            # WTM used nameplate watts — recalculate electricity with actual
            actual_elec = self.daily_electricity_cost(
                effective_watts, location["electricity_cost_kwh"]
            )
            for coin in wtm.get("all_coins", []):
                coin["daily_electricity"] = actual_elec
                coin["daily_profit"] = coin["daily_revenue"] - actual_elec
            if wtm["all_coins"]:
                wtm["all_coins"].sort(key=lambda x: x["daily_profit"], reverse=True)
                best = wtm["all_coins"][0]
                wtm["daily_electricity"] = actual_elec
                wtm["daily_profit"] = best["daily_profit"]
                wtm["monthly_profit"] = best["daily_profit"] * 30

        hrn = self._get_hashrateno_data(miner, location)
        if actual_watts and hrn["available"]:
            # Recalculate Hashrate.no electricity with actual watts
            actual_elec = self.daily_electricity_cost(
                effective_watts, location["electricity_cost_kwh"]
            )
            hrn["daily_electricity"] = actual_elec
            hrn["daily_profit"] = hrn["daily_revenue"] - actual_elec
            hrn["monthly_profit"] = hrn["daily_profit"] * 30

        mn = self._get_miningnow_data(miner)

        # Best daily profit from available sources
        profits = []
        if wtm["available"]:
            profits.append(("whattomine", wtm["daily_profit"]))
        if hrn["available"]:
            profits.append(("hashrateno", hrn["daily_profit"]))
        if profits:
            best_source, best_daily = max(profits, key=lambda x: x[1])
        else:
            best_source, best_daily = None, 0

        # Revenue from the best source; electricity is the same for all sources
        daily_electricity = self.daily_electricity_cost(
            effective_watts, location.get("electricity_cost_kwh", 0.10)
        )
        if best_source == "whattomine":
            daily_revenue = wtm.get("daily_revenue", 0)
        elif best_source == "hashrateno":
            daily_revenue = hrn.get("daily_revenue", 0)
        else:
            daily_revenue = 0

        roi = self.calculate_roi(
            miner.get("purchase_price", 0),
            miner.get("quantity", 1),
            best_daily,
            miner.get("purchase_date", ""),
        )

        return {
            "miner": miner,
            "location": location,
            "power": power_info,
            "sources": {
                "whattomine": wtm,
                "hashrateno": hrn,
                "miningnow": mn,
            },
            "roi": roi,
            "daily_revenue": round(daily_revenue, 2),
            "daily_electricity": round(daily_electricity, 2),
            "best_daily_profit": round(best_daily, 2),
            "best_source": best_source,
            "status": "inactive" if is_inactive else self._get_status(best_daily),
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
                r["power"]["effective_watts"],
                r["location"].get("electricity_cost_kwh", 0.10),
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
