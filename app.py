import logging
import os
import threading
import time as _time
from functools import wraps

import bcrypt
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

import config
from services.cache_manager import CacheManager
from services.inventory_manager import InventoryManager
from services.whattomine_service import WhatToMineService
from services.hashrateno_service import HashrateNoService
from services.miningnow_service import MiningNowService
from services.profitability_engine import ProfitabilityEngine
from services.powerpool_service import PowerPoolService
from services.history_service import HistoryService
from services.coinbase_service import CoinbaseService
from services.power_import import (
    import_power_csv, get_power_data, clear_power_data
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = os.getenv("SECRET_KEY", os.urandom(32))

# --- Auth setup ---
_password_hash = None
if config.DASHBOARD_PASSWORD:
    _password_hash = bcrypt.hashpw(
        config.DASHBOARD_PASSWORD.encode("utf-8"), bcrypt.gensalt()
    )


def _auth_enabled():
    return _password_hash is not None


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if _auth_enabled() and not session.get("authenticated"):
            # For API routes return 401; for pages redirect to login
            if request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


# Initialize services
wtm_cache = CacheManager(str(config.CACHE_DIR / "whattomine"))
hrn_cache = CacheManager(str(config.CACHE_DIR / "hashrateno"))
mn_cache = CacheManager(str(config.CACHE_DIR / "miningnow"))
pp_cache = CacheManager(str(config.CACHE_DIR / "powerpool"))

inventory_mgr = InventoryManager(
    str(config.INVENTORY_FILE), str(config.LOCATIONS_FILE)
)
whattomine_svc = WhatToMineService(wtm_cache)
hashrateno_svc = HashrateNoService(config.HASHRATE_NO_API_KEY, hrn_cache)
miningnow_svc = MiningNowService(mn_cache)
powerpool_svc = PowerPoolService(config.POWERPOOL_OBSERVER_KEY, pp_cache)
engine = ProfitabilityEngine(
    whattomine_svc, hashrateno_svc, miningnow_svc, inventory_mgr
)
history_svc = HistoryService()
cb_cache = CacheManager(str(config.CACHE_DIR / "coinbase"))
coinbase_svc = CoinbaseService(
    config.COINBASE_API_KEY, config.COINBASE_API_SECRET, cb_cache
)


# ---- Background uptime tracker ----

def _uptime_tracker():
    """Background thread: logs PowerPool worker uptime every 5 minutes."""
    _time.sleep(30)  # Wait for app to start
    while True:
        try:
            if powerpool_svc.is_configured():
                miners = inventory_mgr.get_all_miners()
                statuses = powerpool_svc.get_all_worker_statuses(miners)
                history_svc.record_uptime(statuses, miners)
        except Exception as e:
            logger.error("Uptime tracker error: %s", e)
        _time.sleep(300)  # 5 minutes


_tracker_thread = threading.Thread(target=_uptime_tracker, daemon=True)
_tracker_thread.start()


# ---- Auth routes ----

@app.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_enabled():
        return redirect("/")
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (
            username == config.DASHBOARD_USERNAME
            and bcrypt.checkpw(password.encode("utf-8"), _password_hash)
        ):
            session["authenticated"] = True
            next_url = request.args.get("next", "/")
            return redirect(next_url)
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    if _auth_enabled():
        return redirect(url_for("login"))
    return redirect("/")


# ---- Page routes ----

@app.route("/")
@login_required
def index():
    return render_template("index.html", active_page="dashboard")


@app.route("/swap")
@login_required
def swap_page():
    return render_template("swap.html", active_page="swap")


@app.route("/pools")
@login_required
def pools_page():
    return render_template("pools.html", active_page="pools")


@app.route("/optimizer")
@login_required
def optimizer_page():
    return render_template("optimizer.html", active_page="optimizer")


@app.route("/difficulty")
@login_required
def difficulty_page():
    return render_template("difficulty.html", active_page="difficulty")


@app.route("/wallet")
@login_required
def wallet_page():
    return render_template("wallet.html", active_page="wallet")


# ---- Miners API ----

@app.route("/api/miners", methods=["GET"])
@login_required
def list_miners():
    return jsonify(inventory_mgr.get_all_miners())


@app.route("/api/miners", methods=["POST"])
@login_required
def add_miner():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    miner = inventory_mgr.add_miner(data)
    return jsonify(miner), 201


@app.route("/api/miners/<miner_id>", methods=["PUT"])
@login_required
def update_miner(miner_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    miner = inventory_mgr.update_miner(miner_id, data)
    if miner is None:
        return jsonify({"error": "Miner not found"}), 404
    return jsonify(miner)


@app.route("/api/miners/<miner_id>", methods=["DELETE"])
@login_required
def delete_miner(miner_id):
    if inventory_mgr.delete_miner(miner_id):
        return jsonify({"success": True})
    return jsonify({"error": "Miner not found"}), 404


@app.route("/api/miners/<miner_id>/duplicate", methods=["POST"])
@login_required
def duplicate_miner(miner_id):
    miner = inventory_mgr.duplicate_miner(miner_id)
    if miner is None:
        return jsonify({"error": "Miner not found"}), 404
    return jsonify(miner), 201


# ---- Locations API ----

@app.route("/api/locations", methods=["GET"])
@login_required
def list_locations():
    return jsonify(inventory_mgr.get_all_locations())


@app.route("/api/locations", methods=["POST"])
@login_required
def add_location():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    loc = inventory_mgr.add_location(data)
    return jsonify(loc), 201


@app.route("/api/locations/<location_id>", methods=["PUT"])
@login_required
def update_location(location_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    loc = inventory_mgr.update_location(location_id, data)
    if loc is None:
        return jsonify({"error": "Location not found"}), 404
    return jsonify(loc)


@app.route("/api/locations/<location_id>", methods=["DELETE"])
@login_required
def delete_location(location_id):
    if inventory_mgr.delete_location(location_id):
        return jsonify({"success": True})
    return jsonify({"error": "Location not found"}), 404


# ---- Profitability API ----

@app.route("/api/profitability", methods=["GET"])
@login_required
def get_profitability():
    try:
        data = engine.calculate_all()
        # Piggyback: record profit snapshot (self-throttles to once per hour)
        try:
            history_svc.record_profit_snapshot(data.get("miners", []))
        except Exception as e:
            logger.error("Profit snapshot failed: %s", e)
        return jsonify(data)
    except Exception as e:
        logger.error("Profitability calculation failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/profitability/<miner_id>", methods=["GET"])
@login_required
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
@login_required
def wtm_coins():
    return jsonify(whattomine_svc.get_all_coin_names())


@app.route("/api/sources/hashrateno/models", methods=["GET"])
@login_required
def hrn_models():
    return jsonify(hashrateno_svc.get_all_model_names())


@app.route("/api/sources/miningnow/models", methods=["GET"])
@login_required
def mn_models():
    return jsonify(miningnow_svc.get_all_model_names())


@app.route("/api/algorithms", methods=["GET"])
@login_required
def get_algorithms():
    """Return list of supported algorithms from coin_mappings."""
    return jsonify(list(engine.coin_mappings.keys()))


# ---- PowerPool Monitoring API ----

@app.route("/api/pool/workers", methods=["GET"])
@login_required
def pool_workers():
    """Get all PowerPool worker statuses matched to inventory miners."""
    if not powerpool_svc.is_configured():
        return jsonify({"error": "PowerPool observer key not configured"}), 404
    miners = inventory_mgr.get_all_miners()
    statuses = powerpool_svc.get_all_worker_statuses(miners)
    unmatched = powerpool_svc.get_unmatched_workers(miners)
    return jsonify({
        "statuses": statuses,
        "unmatched": [
            {
                "worker_name": w["short_name"],
                "algorithm": w.get("algorithm", ""),
                "hashrate": round(w.get("hashrate", 0), 2),
                "hashrate_units": w.get("hashrate_units", ""),
                "online": w.get("online", False),
            }
            for w in unmatched
        ],
        "configured": True,
        "cache_age": powerpool_svc.get_cache_age(),
    })


@app.route("/api/pool/overview", methods=["GET"])
@login_required
def pool_overview():
    """Get PowerPool mining overview (per-algorithm totals)."""
    if not powerpool_svc.is_configured():
        return jsonify({"error": "PowerPool observer key not configured"}), 404
    return jsonify(powerpool_svc.get_mining_overview())


@app.route("/api/pool/revenue", methods=["GET"])
@login_required
def pool_revenue():
    """Get PowerPool balance/earnings."""
    if not powerpool_svc.is_configured():
        return jsonify({"error": "PowerPool observer key not configured"}), 404
    return jsonify(powerpool_svc.get_revenue())


# ---- CSV Power Import ----

@app.route("/api/power-import/upload", methods=["POST"])
@login_required
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
@login_required
def power_data():
    return jsonify(get_power_data())


@app.route("/api/power-import/clear", methods=["POST"])
@login_required
def power_clear():
    clear_power_data()
    return jsonify({"success": True})


# ---- Coinbase Wallet API ----

@app.route("/api/wallet/portfolio", methods=["GET"])
@login_required
def wallet_portfolio():
    """Get Coinbase wallet portfolio summary."""
    if not coinbase_svc.is_configured():
        return jsonify({"error": "Coinbase API not configured"}), 404
    try:
        data = coinbase_svc.get_portfolio_summary()
        data["cache_age"] = coinbase_svc.get_cache_age()
        return jsonify(data)
    except Exception as e:
        logger.error("Coinbase portfolio error: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/wallet/accounts", methods=["GET"])
@login_required
def wallet_accounts():
    """Get all Coinbase accounts with non-zero balances."""
    if not coinbase_svc.is_configured():
        return jsonify({"error": "Coinbase API not configured"}), 404
    try:
        accounts = coinbase_svc.get_accounts()
        return jsonify(accounts)
    except Exception as e:
        logger.error("Coinbase accounts error: %s", e)
        return jsonify({"error": str(e)}), 500


# ---- Cache management ----

@app.route("/api/cache/refresh", methods=["POST"])
@login_required
def refresh_all_caches():
    wtm_cache.invalidate_all()
    hrn_cache.invalidate_all()
    mn_cache.invalidate_all()
    pp_cache.invalidate_all()
    cb_cache.invalidate_all()
    return jsonify({"success": True, "message": "All caches cleared"})


@app.route("/api/cache/refresh/<source>", methods=["POST"])
@login_required
def refresh_source_cache(source):
    caches = {
        "whattomine": wtm_cache,
        "hashrateno": hrn_cache,
        "miningnow": mn_cache,
        "powerpool": pp_cache,
        "coinbase": cb_cache,
    }
    cache = caches.get(source)
    if not cache:
        return jsonify({"error": "Unknown source"}), 400
    cache.invalidate_all()
    return jsonify({"success": True, "message": f"{source} cache cleared"})


@app.route("/api/cache/status", methods=["GET"])
@login_required
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
        "powerpool": {
            "age_seconds": powerpool_svc.get_cache_age(),
            "ttl_seconds": 120,
            "configured": powerpool_svc.is_configured(),
        },
    })


# ---- History & Analytics API ----

@app.route("/api/history/profit", methods=["GET"])
@login_required
def profit_history():
    """Get daily profit history for chart rendering."""
    days = request.args.get("days", 30, type=int)
    miner_id = request.args.get("miner_id")
    return jsonify(history_svc.get_profit_history(days=days, miner_id=miner_id))


@app.route("/api/history/uptime", methods=["GET"])
@login_required
def uptime_stats():
    """Get uptime stats per miner."""
    days = request.args.get("days", 7, type=int)
    return jsonify(history_svc.get_uptime_stats(days=days))


@app.route("/api/alerts/coin-switch", methods=["GET"])
@login_required
def coin_switch_alerts():
    """Check if any algorithm has a more profitable coin than the primary."""
    return jsonify(engine.get_coin_switch_alerts())


# ---- Analysis Tools API ----

@app.route("/api/tools/swap-compare", methods=["POST"])
@login_required
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
@login_required
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
@login_required
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
