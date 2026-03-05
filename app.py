import logging

from flask import Flask, jsonify, render_template, request

import config
from services.cache_manager import CacheManager
from services.inventory_manager import InventoryManager
from services.whattomine_service import WhatToMineService
from services.hashrateno_service import HashrateNoService
from services.miningnow_service import MiningNowService
from services.profitability_engine import ProfitabilityEngine
from services.power_import import (
    import_power_csv, get_power_data, clear_power_data
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

# Initialize services
wtm_cache = CacheManager(str(config.CACHE_DIR / "whattomine"))
hrn_cache = CacheManager(str(config.CACHE_DIR / "hashrateno"))
mn_cache = CacheManager(str(config.CACHE_DIR / "miningnow"))

inventory_mgr = InventoryManager(
    str(config.INVENTORY_FILE), str(config.LOCATIONS_FILE)
)
whattomine_svc = WhatToMineService(wtm_cache)
hashrateno_svc = HashrateNoService(config.HASHRATE_NO_API_KEY, hrn_cache)
miningnow_svc = MiningNowService(mn_cache)
engine = ProfitabilityEngine(
    whattomine_svc, hashrateno_svc, miningnow_svc, inventory_mgr
)


# ---- Page routes ----

@app.route("/")
def index():
    return render_template("index.html")


# ---- Miners API ----

@app.route("/api/miners", methods=["GET"])
def list_miners():
    return jsonify(inventory_mgr.get_all_miners())


@app.route("/api/miners", methods=["POST"])
def add_miner():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    miner = inventory_mgr.add_miner(data)
    return jsonify(miner), 201


@app.route("/api/miners/<miner_id>", methods=["PUT"])
def update_miner(miner_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    miner = inventory_mgr.update_miner(miner_id, data)
    if miner is None:
        return jsonify({"error": "Miner not found"}), 404
    return jsonify(miner)


@app.route("/api/miners/<miner_id>", methods=["DELETE"])
def delete_miner(miner_id):
    if inventory_mgr.delete_miner(miner_id):
        return jsonify({"success": True})
    return jsonify({"error": "Miner not found"}), 404


@app.route("/api/miners/<miner_id>/duplicate", methods=["POST"])
def duplicate_miner(miner_id):
    miner = inventory_mgr.duplicate_miner(miner_id)
    if miner is None:
        return jsonify({"error": "Miner not found"}), 404
    return jsonify(miner), 201


# ---- Locations API ----

@app.route("/api/locations", methods=["GET"])
def list_locations():
    return jsonify(inventory_mgr.get_all_locations())


@app.route("/api/locations", methods=["POST"])
def add_location():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    loc = inventory_mgr.add_location(data)
    return jsonify(loc), 201


@app.route("/api/locations/<location_id>", methods=["PUT"])
def update_location(location_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    loc = inventory_mgr.update_location(location_id, data)
    if loc is None:
        return jsonify({"error": "Location not found"}), 404
    return jsonify(loc)


@app.route("/api/locations/<location_id>", methods=["DELETE"])
def delete_location(location_id):
    if inventory_mgr.delete_location(location_id):
        return jsonify({"success": True})
    return jsonify({"error": "Location not found"}), 404


# ---- Profitability API ----

@app.route("/api/profitability", methods=["GET"])
def get_profitability():
    try:
        data = engine.calculate_all()
        return jsonify(data)
    except Exception as e:
        logger.error("Profitability calculation failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/profitability/<miner_id>", methods=["GET"])
def get_miner_profitability(miner_id):
    miner = inventory_mgr.get_miner(miner_id)
    if not miner:
        return jsonify({"error": "Miner not found"}), 404
    location = inventory_mgr.get_location(miner.get("location_id", ""))
    if not location:
        location = {
            "id": "", "name": "Unknown",
            "electricity_cost_kwh": 0.10, "currency": "USD",
        }
    try:
        data = engine.calculate_for_miner(miner, location, primary_only=False)
        return jsonify(data)
    except Exception as e:
        logger.error("Profitability calculation failed for %s: %s", miner_id, e)
        return jsonify({"error": str(e)}), 500


# ---- Data source helpers ----

@app.route("/api/sources/whattomine/coins", methods=["GET"])
def wtm_coins():
    return jsonify(whattomine_svc.get_all_coin_names())


@app.route("/api/sources/hashrateno/models", methods=["GET"])
def hrn_models():
    return jsonify(hashrateno_svc.get_all_model_names())


@app.route("/api/sources/miningnow/models", methods=["GET"])
def mn_models():
    return jsonify(miningnow_svc.get_all_model_names())


@app.route("/api/algorithms", methods=["GET"])
def get_algorithms():
    """Return list of supported algorithms from coin_mappings."""
    return jsonify(list(engine.coin_mappings.keys()))


# ---- CSV Power Import ----

@app.route("/api/power-import/upload", methods=["POST"])
def power_import():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    if not file.filename or not file.filename.endswith(".csv"):
        return jsonify({"error": "Please upload a CSV file"}), 400
    csv_content = file.read().decode("utf-8-sig")
    result = import_power_csv(csv_content)
    if "error" in result:
        return jsonify(result), 400
    return jsonify(result)


@app.route("/api/power-import/data", methods=["GET"])
def power_data():
    return jsonify(get_power_data())


@app.route("/api/power-import/clear", methods=["POST"])
def power_clear():
    clear_power_data()
    return jsonify({"success": True})


# ---- Cache management ----

@app.route("/api/cache/refresh", methods=["POST"])
def refresh_all_caches():
    wtm_cache.invalidate_all()
    hrn_cache.invalidate_all()
    mn_cache.invalidate_all()
    return jsonify({"success": True, "message": "All caches cleared"})


@app.route("/api/cache/refresh/<source>", methods=["POST"])
def refresh_source_cache(source):
    caches = {
        "whattomine": wtm_cache,
        "hashrateno": hrn_cache,
        "miningnow": mn_cache,
    }
    cache = caches.get(source)
    if not cache:
        return jsonify({"error": "Unknown source"}), 400
    cache.invalidate_all()
    return jsonify({"success": True, "message": f"{source} cache cleared"})


@app.route("/api/cache/status", methods=["GET"])
def cache_status():
    return jsonify({
        "whattomine": {
            "age_seconds": wtm_cache.get_age_seconds("coins_index"),
            "ttl_seconds": config.WHATTOMINE_CACHE_TTL,
        },
        "hashrateno": {
            "age_seconds": hashrateno_svc.get_cache_age(),
            "ttl_seconds": config.HASHRATENO_CACHE_TTL,
            "configured": hashrateno_svc.is_configured(),
        },
        "miningnow": {
            "age_seconds": mn_cache.get_age_seconds("miner_list"),
            "ttl_seconds": config.MININGNOW_CACHE_TTL,
        },
    })


# ---- Analysis Tools API ----

@app.route("/api/tools/swap-compare", methods=["POST"])
def swap_compare():
    """Compare current miner vs a hypothetical replacement."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    current_id = data.get("current_miner_id")
    replacement = data.get("replacement", {})

    current_miner = inventory_mgr.get_miner(current_id)
    if not current_miner:
        return jsonify({"error": "Current miner not found"}), 404

    location = inventory_mgr.get_location(current_miner.get("location_id", ""))
    if not location:
        location = {"id": "", "name": "Unknown", "electricity_cost_kwh": 0.10, "currency": "USD"}

    # Calculate current miner profitability
    current_result = engine.calculate_for_miner(current_miner, location)

    # Build a temporary miner dict for the replacement
    rep_miner = {
        "name": replacement.get("model", "Replacement"),
        "model": replacement.get("model", ""),
        "type": "ASIC",
        "algorithm": replacement.get("algorithm", current_miner.get("algorithm", "")),
        "hashrate": replacement.get("hashrate", 0),
        "hashrate_unit": replacement.get("hashrate_unit", current_miner.get("hashrate_unit", "TH/s")),
        "wattage": replacement.get("wattage", 0),
        "location_id": current_miner.get("location_id", ""),
        "quantity": 1,
        "purchase_price": replacement.get("purchase_price", 0),
        "status": "active",
        "hashrateno_model_key": replacement.get("model", ""),
        "miningnow_model_key": replacement.get("model", ""),
    }

    rep_result = engine.calculate_for_miner(rep_miner, location)

    current_profit = current_result.get("best_daily_profit", 0)
    rep_profit = rep_result.get("best_daily_profit", 0)
    profit_delta = rep_profit - current_profit
    rep_cost = replacement.get("purchase_price", 0)
    resale_value = replacement.get("resale_current", 0)
    net_cost = rep_cost - resale_value

    days_to_breakeven = int(net_cost / profit_delta) if profit_delta > 0 and net_cost > 0 else -1

    return jsonify({
        "current": {
            "name": current_miner.get("name"),
            "model": current_miner.get("model"),
            "daily_revenue": current_result.get("daily_revenue", 0),
            "daily_electricity": current_result.get("daily_electricity", 0),
            "daily_profit": current_profit,
            "wattage": current_miner.get("wattage", 0),
            "profit_per_kw": round(current_profit / (current_miner.get("wattage", 1) / 1000), 2) if current_miner.get("wattage", 0) > 0 else 0,
        },
        "replacement": {
            "model": rep_miner["model"],
            "daily_revenue": rep_result.get("daily_revenue", 0),
            "daily_electricity": rep_result.get("daily_electricity", 0),
            "daily_profit": rep_profit,
            "wattage": rep_miner["wattage"],
            "profit_per_kw": round(rep_profit / (rep_miner["wattage"] / 1000), 2) if rep_miner["wattage"] > 0 else 0,
        },
        "comparison": {
            "profit_delta": round(profit_delta, 2),
            "monthly_delta": round(profit_delta * 30, 2),
            "yearly_delta": round(profit_delta * 365, 2),
            "replacement_cost": rep_cost,
            "resale_value": resale_value,
            "net_cost": round(net_cost, 2),
            "days_to_breakeven": days_to_breakeven,
        },
    })


@app.route("/api/tools/power-optimize", methods=["POST"])
def power_optimize():
    """Find optimal miner combination within a wattage budget."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    max_watts = data.get("max_watts", 0)
    if max_watts <= 0:
        return jsonify({"error": "max_watts must be positive"}), 400

    # Get current profitability data
    prof_data = engine.calculate_all()
    miners = prof_data.get("miners", [])

    # Build list of active miners with their profit/watt data
    candidates = []
    for r in miners:
        if r["status"] == "inactive":
            continue
        m = r["miner"]
        watts = r["power"]["effective_watts"] if r.get("power") else m.get("wattage", 0)
        profit = r.get("best_daily_profit", 0)
        qty = m.get("quantity", 1)
        # Treat each unit individually for the optimizer
        for i in range(qty):
            candidates.append({
                "name": m.get("name", ""),
                "model": m.get("model", ""),
                "watts": watts,
                "daily_profit": profit,
                "profit_per_kw": round(profit / (watts / 1000), 2) if watts > 0 else 0,
                "miner_id": m.get("id", ""),
            })

    # Greedy: sort by profit per watt descending, pack into budget
    candidates.sort(key=lambda c: c["profit_per_kw"], reverse=True)

    selected = []
    remaining_watts = max_watts
    for c in candidates:
        if c["watts"] <= remaining_watts and c["daily_profit"] > 0:
            selected.append(c)
            remaining_watts -= c["watts"]

    total_watts = sum(c["watts"] for c in selected)
    total_profit = sum(c["daily_profit"] for c in selected)

    # Also show which miners didn't make the cut
    selected_ids = {(c["name"], i) for i, c in enumerate(selected)}
    excluded = [c for c in candidates if c not in selected and c["daily_profit"] > 0]

    return jsonify({
        "budget_watts": max_watts,
        "used_watts": total_watts,
        "remaining_watts": remaining_watts,
        "total_daily_profit": round(total_profit, 2),
        "total_monthly_profit": round(total_profit * 30, 2),
        "selected": selected,
        "excluded": excluded,
    })


@app.route("/api/tools/difficulty", methods=["GET"])
def difficulty_history():
    """Fetch difficulty history from free APIs."""
    import requests as req

    algo = request.args.get("algo", "SHA-256")
    results = {}

    try:
        if algo == "SHA-256":
            # blockchain.com free API for BTC difficulty
            resp = req.get(
                "https://api.blockchain.info/charts/difficulty",
                params={"timespan": "180days", "format": "json"},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                results["coin"] = "BTC"
                results["algorithm"] = "SHA-256"
                results["data"] = [
                    {"timestamp": p["x"], "difficulty": p["y"]}
                    for p in data.get("values", [])
                ]
        elif algo == "Scrypt":
            # bitinfocharts via CoinGecko-style — use blockchain endpoint
            resp = req.get(
                "https://api.blockchair.com/litecoin/stats",
                timeout=10,
            )
            if resp.ok:
                data = resp.json().get("data", {})
                results["coin"] = "LTC"
                results["algorithm"] = "Scrypt"
                results["current_difficulty"] = data.get("difficulty")
                results["hashrate_24h"] = data.get("hashrate_24h")
                results["data"] = []  # Blockchair doesn't give historical for free
        elif algo == "Equihash":
            resp = req.get(
                "https://api.blockchair.com/zcash/stats",
                timeout=10,
            )
            if resp.ok:
                data = resp.json().get("data", {})
                results["coin"] = "ZEC"
                results["algorithm"] = "Equihash"
                results["current_difficulty"] = data.get("difficulty")
                results["data"] = []
    except Exception as e:
        logger.error("Difficulty fetch error: %s", e)
        return jsonify({"error": str(e)}), 500

    return jsonify(results)


if __name__ == "__main__":
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
