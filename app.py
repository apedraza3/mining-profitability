import logging

from flask import Flask, jsonify, render_template, request

import config
from services.cache_manager import CacheManager
from services.inventory_manager import InventoryManager
from services.whattomine_service import WhatToMineService
from services.hashrateno_service import HashrateNoService
from services.miningnow_service import MiningNowService
from services.profitability_engine import ProfitabilityEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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
        data = engine.calculate_for_miner(miner, location)
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


if __name__ == "__main__":
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
