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
    def effective_elec_rate(location: dict, total_location_watts: int = 0) -> tuple[float, float]:
        """Calculate effective electricity rate after solar offset.
        Returns (effective_rate, solar_offset_pct) where offset is 0.0 to 1.0."""
        base_rate = location.get("electricity_cost_kwh", 0.10)
        solar_kwh = location.get("solar_daily_kwh", 0)
        if not solar_kwh or solar_kwh <= 0 or total_location_watts <= 0:
            return base_rate, 0.0
        mining_daily_kwh = total_location_watts * 24 / 1000
        offset = min(solar_kwh / mining_daily_kwh, 1.0)
        return round(base_rate * (1 - offset), 6), round(offset, 4)

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
        self, miner: dict, location: dict, primary_only: bool = True,
        solar_info: dict | None = None,
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

        # Breakeven electricity rate: $/kWh at which this miner's profit = $0
        daily_kwh = effective_watts * 24 / 1000
        breakeven_elec_rate = round(daily_revenue / daily_kwh, 4) if daily_kwh > 0 and daily_revenue > 0 else 0

        # Solar-adjusted electricity cost
        si = solar_info or {}
        solar_offset_pct = si.get("solar_offset_pct", 0)
        if solar_offset_pct > 0:
            solar_elec = round(daily_electricity * (1 - solar_offset_pct), 2)
            solar_profit = round(daily_revenue - solar_elec, 2)
            solar_savings = round(daily_electricity - solar_elec, 2)
        else:
            solar_elec = None
            solar_profit = None
            solar_savings = None

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
            "breakeven_elec_rate": breakeven_elec_rate,
            "solar": {
                "offset_pct": solar_offset_pct,
                "daily_electricity": solar_elec,
                "daily_profit": solar_profit,
                "daily_savings": solar_savings,
            },
            "status": "inactive" if is_inactive else self._get_status(best_daily),
        }

    def _fetch_electricity_data(self) -> dict | None:
        """Fetch live solar + settings + bill estimate from electricity dashboard."""
        import os
        try:
            import requests
            api = os.getenv("ELECTRICITY_API_URL", "http://127.0.0.1:5001")
            realtime = None
            settings = None
            bill_estimate = None
            resp = requests.get(f"{api}/api/realtime", timeout=3)
            if resp.ok:
                data = resp.json()
                if data.get("status") == "ok":
                    realtime = data
            resp2 = requests.get(f"{api}/api/settings", timeout=3)
            if resp2.ok:
                settings = resp2.json()
            resp3 = requests.get(f"{api}/api/bill-estimate", timeout=3)
            if resp3.ok:
                bill_estimate = resp3.json()
            return {"realtime": realtime, "settings": settings, "bill_estimate": bill_estimate}
        except Exception as e:
            logger.debug("Electricity dashboard fetch failed: %s", e)
        return None

    def _inject_live_solar(self, locations: dict) -> dict:
        """Inject live solar production and demand rate into home location.

        Returns electricity settings dict (demand_rate, bill_estimate, etc.) or empty dict.
        """
        elec_data = self._fetch_electricity_data()
        if not elec_data:
            return {}

        settings = elec_data.get("settings") or {}
        bill_estimate = elec_data.get("bill_estimate")
        if bill_estimate:
            settings["_bill_estimate"] = bill_estimate
        solar_data = elec_data.get("realtime")
        if not solar_data:
            return settings

        solar_w = solar_data.get("solar_production_w", 0)
        consumption_w = solar_data.get("house_consumption_w", 0)
        crypto_w = solar_data.get("crypto_mining_w", 0)

        # Find the home location and inject solar + demand data
        for loc_id, loc in locations.items():
            if loc.get("name", "").lower() == "home":
                # Inject demand rate for cost calculations
                loc["_demand_rate"] = settings.get("demand_rate", 15.38)

                if solar_w > 0:
                    # Estimate daily solar kWh: current production * typical sun hours
                    # Use crypto's proportional share of solar, not all of it
                    if consumption_w > 0:
                        crypto_share = min(crypto_w / consumption_w, 1.0)
                        crypto_solar_w = min(solar_w, consumption_w) * crypto_share
                    else:
                        crypto_solar_w = 0
                    # Convert current watts to estimated daily kWh (use 5 peak sun hours)
                    solar_daily_kwh = crypto_solar_w * 5 / 1000
                    loc["solar_daily_kwh"] = solar_daily_kwh
                    loc["_live_solar"] = {
                        "solar_w": solar_w,
                        "consumption_w": consumption_w,
                        "crypto_w": crypto_w,
                        "crypto_solar_w": crypto_solar_w,
                    }
                    logger.debug(
                        "Injected live solar: %.0fW solar, %.0fW crypto share → %.1f kWh/day",
                        solar_w, crypto_solar_w, solar_daily_kwh,
                    )
                break

        return settings

    def generate_suggestions(
        self, results: list, summary: dict, by_location: dict,
        demand_rate: float = 0, home_demand: dict | None = None,
        total_home_demand_charge: float = 0,
    ) -> list:
        """Generate actionable suggestions based on profitability data."""
        suggestions = []
        active = [r for r in results if r["status"] != "inactive"]
        home_demand = home_demand or {}

        # 1. Demand charge warning — this is often a hidden cost
        if total_home_demand_charge > 0:
            home_miners = [r for r in active if r["location"].get("name", "").lower() == "home"]
            total_home_kw = sum(d["kw"] for d in home_demand.values())
            suggestions.append({
                "type": "demand_charge",
                "priority": "high",
                "message": f"Home miners add {total_home_kw:.1f} kW peak demand, costing ${total_home_demand_charge:.2f}/mo in demand charges (${demand_rate:.2f}/kW). This is on top of energy costs.",
            })

            # Per-miner demand charge impact for unprofitable/marginal miners
            for r in home_miners:
                name = r["miner"].get("name", "Unknown")
                d = home_demand.get(name, {})
                if d and r["status"] in ("unprofitable", "marginal"):
                    demand_monthly = d["monthly_demand_charge"]
                    daily_profit = r.get("best_daily_profit", 0)
                    profit_after_demand = daily_profit - (demand_monthly / 30)
                    if profit_after_demand < 0:
                        suggestions.append({
                            "type": "demand_unprofitable",
                            "priority": "high",
                            "miner": name,
                            "message": f"{name} earns ${daily_profit:.2f}/day but its {d['kw']:.1f} kW adds ${demand_monthly:.2f}/mo in demand charges. True profit: ${profit_after_demand:.2f}/day.",
                        })

        # 2. Unprofitable miners — suggest shutdown
        unprofitable = [r for r in active if r["status"] == "unprofitable"]
        for r in unprofitable:
            name = r["miner"].get("name", "Unknown")
            daily_loss = abs(r.get("best_daily_profit", 0))
            monthly_loss = daily_loss * 30
            demand_savings = home_demand.get(name, {}).get("monthly_demand_charge", 0)
            total_monthly_savings = monthly_loss + demand_savings
            msg = f"{name} is losing ${daily_loss:.2f}/day (${monthly_loss:.2f}/mo)."
            if demand_savings > 0:
                msg += f" Plus ${demand_savings:.2f}/mo in demand charges. Total savings if shut down: ${total_monthly_savings:.2f}/mo."
            else:
                msg += " Consider shutting it down."
            suggestions.append({
                "type": "shutdown",
                "priority": "high",
                "miner": name,
                "message": msg,
                "savings": total_monthly_savings,
            })

        # 3. Marginal miners — warn
        marginal = [r for r in active if r["status"] == "marginal"]
        for r in marginal:
            name = r["miner"].get("name", "Unknown")
            daily = r.get("best_daily_profit", 0)
            loc_name = r["location"].get("name", "")
            solar = r["solar"]
            if solar.get("offset_pct", 0) > 0 and solar.get("daily_profit") is not None:
                if solar["daily_profit"] > daily:
                    suggestions.append({
                        "type": "solar_helps",
                        "priority": "info",
                        "miner": name,
                        "message": f"{name} earns ${daily:.2f}/day but ${solar['daily_profit']:.2f}/day with solar offset.",
                    })
            elif loc_name.lower() == "home" and daily < 0.50:
                suggestions.append({
                    "type": "marginal_warning",
                    "priority": "medium",
                    "miner": name,
                    "message": f"{name} is barely profitable at ${daily:.2f}/day. Monitor closely.",
                })

        # 4. Total fleet savings from shutting down losers
        total_loss = sum(abs(r.get("best_daily_profit", 0)) for r in unprofitable)
        total_demand_savings = sum(
            home_demand.get(r["miner"].get("name", ""), {}).get("monthly_demand_charge", 0)
            for r in unprofitable
        )
        if total_loss > 0:
            msg = f"Shutting down all unprofitable miners would save ${total_loss:.2f}/day (${total_loss * 30:.2f}/mo) in energy."
            if total_demand_savings > 0:
                msg += f" Plus ${total_demand_savings:.2f}/mo in reduced demand charges."
            suggestions.append({
                "type": "fleet_savings",
                "priority": "high",
                "message": msg,
                "savings": total_loss * 30 + total_demand_savings,
            })

        # 5. Solar benefit summary for home miners
        home_miners_list = [r for r in active if r["location"].get("name", "").lower() == "home"]
        home_solar_savings = sum((r["solar"].get("daily_savings") or 0) for r in home_miners_list)
        if home_solar_savings > 0:
            suggestions.append({
                "type": "solar_summary",
                "priority": "info",
                "message": f"Solar is saving ${home_solar_savings:.2f}/day (${home_solar_savings * 30:.2f}/mo) on home miners.",
            })

        # 6. Peak demand reduction tip
        if total_home_demand_charge > 0 and len(home_miners_list) > 1:
            # Find the most power-hungry marginal/unprofitable home miner
            candidates = sorted(
                [r for r in home_miners_list if r["status"] in ("marginal", "unprofitable")],
                key=lambda r: r["power"]["effective_watts"],
                reverse=True,
            )
            if candidates:
                worst = candidates[0]
                worst_name = worst["miner"].get("name", "Unknown")
                worst_kw = worst["power"]["effective_watts"] / 1000
                saved = round(worst_kw * demand_rate, 2)
                suggestions.append({
                    "type": "peak_reduction",
                    "priority": "medium",
                    "miner": worst_name,
                    "message": f"Shutting down {worst_name} ({worst_kw:.1f} kW) would reduce peak demand by {worst_kw:.1f} kW, saving ${saved:.2f}/mo in demand charges alone.",
                })

        # 7. Efficiency comparison — suggest moving miners to cheaper locations
        if len(by_location) > 1:
            loc_rates = {}
            for loc_name, loc_data in by_location.items():
                loc_rates[loc_name] = loc_data.get("electricity_cost_kwh", 0)
            cheapest_loc = min(loc_rates, key=loc_rates.get)
            cheapest_rate = loc_rates[cheapest_loc]
            for r in active:
                loc_name = r["location"].get("name", "")
                rate = r["location"].get("electricity_cost_kwh", 0)
                if rate > cheapest_rate * 1.2 and r["status"] in ("marginal", "unprofitable"):
                    name = r["miner"].get("name", "Unknown")
                    suggestions.append({
                        "type": "relocate",
                        "priority": "medium",
                        "miner": name,
                        "message": f"{name} pays ${rate:.4f}/kWh at {loc_name}. Moving to {cheapest_loc} (${cheapest_rate:.4f}/kWh) could improve profit.",
                    })

        # Sort: high priority first
        priority_order = {"high": 0, "medium": 1, "info": 2}
        suggestions.sort(key=lambda s: priority_order.get(s.get("priority", "info"), 9))

        return suggestions

    def calculate_all(self) -> dict:
        """Calculate profitability for all miners in inventory."""
        miners = self.inventory.get_all_miners()
        locations = {l["id"]: l for l in self.inventory.get_all_locations()}

        # Inject live solar data and demand rate from electricity dashboard
        elec_settings = self._inject_live_solar(locations)

        # Pre-calculate total wattage per location for solar offset distribution
        location_watts = {}
        for miner in miners:
            if miner.get("status") == "inactive":
                continue
            loc_id = miner.get("location_id", "")
            w = miner.get("wattage", 0) * miner.get("quantity", 1)
            location_watts[loc_id] = location_watts.get(loc_id, 0) + w

        # Calculate effective electricity rates per location (after solar)
        location_solar = {}
        for loc_id, loc in locations.items():
            total_w = location_watts.get(loc_id, 0)
            eff_rate, offset_pct = self.effective_elec_rate(loc, total_w)
            location_solar[loc_id] = {
                "effective_rate": eff_rate,
                "solar_offset_pct": offset_pct,
                "solar_daily_kwh": loc.get("solar_daily_kwh", 0),
                "mining_daily_kwh": total_w * 24 / 1000 if total_w > 0 else 0,
            }

        results = []
        for miner in miners:
            loc_id = miner.get("location_id", "")
            location = locations.get(loc_id, {
                "id": loc_id,
                "name": loc_id or "Unknown",
                "electricity_cost_kwh": 0.10,
                "currency": "USD",
            })
            solar_info = location_solar.get(loc_id, {})
            result = self.calculate_for_miner(miner, location, solar_info=solar_info)
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

        # Solar-adjusted totals
        total_solar_savings = sum(
            (r["solar"].get("daily_savings") or 0) * r["miner"].get("quantity", 1)
            for r in active
        )
        total_solar_elec = total_daily_elec - total_solar_savings
        total_solar_profit = total_daily_revenue - total_solar_elec

        # By location
        by_location = {}
        for r in active:
            loc_name = r["location"].get("name", "Unknown")
            loc_id = r["location"].get("id", "")
            if loc_name not in by_location:
                solar = location_solar.get(loc_id, {})
                by_location[loc_name] = {
                    "electricity_cost_kwh": r["location"].get("electricity_cost_kwh", 0),
                    "solar_daily_kwh": solar.get("solar_daily_kwh", 0),
                    "solar_offset_pct": solar.get("solar_offset_pct", 0),
                    "miners": 0,
                    "units": 0,
                    "daily_profit": 0,
                }
            by_location[loc_name]["miners"] += 1
            by_location[loc_name]["units"] += r["miner"].get("quantity", 1)
            by_location[loc_name]["daily_profit"] += (
                r.get("best_daily_profit", 0) * r["miner"].get("quantity", 1)
            )

        # Calculate demand charges using actual measured peak from electricity dashboard
        # We track the highest observed peak per billing month (high water mark)
        demand_rate = elec_settings.get("demand_rate", 0)
        bill_estimate = elec_settings.get("_bill_estimate") or {}
        actual_peak_kw = bill_estimate.get("peak_demand_kw", 0)
        home_demand = {}

        if demand_rate > 0 and actual_peak_kw > 0:
            # Store highest peak — only goes up, never down within a billing month
            from services.history_service import HistoryService
            peak_svc = HistoryService()
            total_home_kw = peak_svc.update_peak_demand(actual_peak_kw)
            total_home_demand_charge = round(total_home_kw * demand_rate, 2)
        elif demand_rate > 0:
            # Fallback: sum miner rated wattages if electricity dashboard unavailable
            for r in active:
                if r["location"].get("name", "").lower() == "home":
                    w = r["power"]["effective_watts"] * r["miner"].get("quantity", 1)
                    home_demand[r["miner"].get("name", "")] = {
                        "watts": w,
                        "kw": w / 1000,
                        "monthly_demand_charge": round((w / 1000) * demand_rate, 2),
                    }
            total_home_kw = sum(d["kw"] for d in home_demand.values())
            total_home_demand_charge = round(total_home_kw * demand_rate, 2)
        else:
            total_home_kw = 0
            total_home_demand_charge = 0

        # Generate suggestions
        suggestions = self.generate_suggestions(
            results, None, by_location,
            demand_rate=demand_rate,
            home_demand=home_demand,
            total_home_demand_charge=total_home_demand_charge,
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
            "suggestions": suggestions,
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
                "total_solar_savings": round(total_solar_savings, 2),
                "total_solar_electricity": round(total_solar_elec, 2),
                "total_solar_profit": round(total_solar_profit, 2),
                "demand_rate": demand_rate,
                "home_mining_kw": round(total_home_kw, 2),
                "home_demand_charge": total_home_demand_charge,
            },
            "by_location": by_location,
            "last_updated": datetime.now().isoformat(),
            "cache_status": cache_status,
        }

    def get_coin_switch_alerts(self) -> list[dict]:
        """Check if any algorithm has a more profitable coin than the primary."""
        alerts = []
        for algo, coins in self.coin_mappings.items():
            if len(coins) <= 1:
                continue

            refs = []
            for coin in coins:
                ref = self.whattomine.get_coin_reference_data(coin["coin_id"])
                if ref and "revenue" in ref:
                    rev_str = ref.get("revenue", "0")
                    if isinstance(rev_str, str):
                        rev = float(
                            rev_str.replace("$", "").replace(",", "").strip() or "0"
                        )
                    else:
                        rev = float(rev_str or 0)
                    refs.append({"coin": coin, "revenue_per_unit": rev})

            if len(refs) < 2:
                continue

            refs.sort(key=lambda x: x["revenue_per_unit"], reverse=True)
            best = refs[0]
            primary_coin_id = coins[0]["coin_id"]
            current = next(
                (r for r in refs if r["coin"]["coin_id"] == primary_coin_id), None
            )

            if (
                current
                and best["coin"]["coin_id"] != primary_coin_id
                and current["revenue_per_unit"] > 0
            ):
                gain_pct = round(
                    (best["revenue_per_unit"] / current["revenue_per_unit"] - 1) * 100,
                    1,
                )
                if gain_pct >= 5:
                    alerts.append(
                        {
                            "algorithm": algo,
                            "current_coin": current["coin"]["tag"],
                            "better_coin": best["coin"]["tag"],
                            "better_coin_name": best["coin"]["name"],
                            "gain_pct": gain_pct,
                        }
                    )

        return alerts

    @staticmethod
    def _format_age(seconds: int | None) -> str:
        if seconds is None:
            return "No data"
        if seconds < 60:
            return f"{seconds}s ago"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"
