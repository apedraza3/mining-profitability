import json
import logging
import os
import threading
import time as _time
from functools import wraps

import bcrypt
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for

import config
from services.cache_manager import CacheManager
from services.inventory_manager import InventoryManager
from services.whattomine_service import WhatToMineService
from services.hashrateno_service import HashrateNoService
from services.profitability_engine import ProfitabilityEngine
from services.powerpool_service import PowerPoolService
from services.history_service import HistoryService
from services.alert_service import AlertService
from services.coinbase_service import CoinbaseService
from services.power_import import (
    import_power_csv, get_power_data, clear_power_data
)
from services.pdu_service import PDUService
from services.tax_export_service import TaxExportService
from services.tou_service import TOUService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB upload limit
_env_secret = os.getenv("SECRET_KEY", "")
if not _env_secret:
    _secret_file = config.DATA_DIR / ".flask_secret"
    if _secret_file.exists():
        _env_secret = _secret_file.read_text().strip()
    else:
        _env_secret = os.urandom(32).hex()
        _secret_file.write_text(_env_secret)
        logger.info("Generated persistent SECRET_KEY at %s", _secret_file)
app.secret_key = _env_secret

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
pp_cache = CacheManager(str(config.CACHE_DIR / "powerpool"))

inventory_mgr = InventoryManager(
    str(config.INVENTORY_FILE), str(config.LOCATIONS_FILE)
)
whattomine_svc = WhatToMineService(wtm_cache)
hashrateno_svc = HashrateNoService(config.HASHRATE_NO_API_KEY, hrn_cache)
powerpool_svc = PowerPoolService(config.POWERPOOL_OBSERVER_KEY, pp_cache)
history_svc = HistoryService()
tou_svc = TOUService(history_svc)
engine = ProfitabilityEngine(
    whattomine_svc, hashrateno_svc, inventory_mgr,
    history_svc=history_svc, tou_svc=tou_svc,
)
alert_svc = AlertService(history_svc)
pdu_svc = PDUService(history_svc)
tax_export_svc = TaxExportService(history_svc)
cb_cache = CacheManager(str(config.CACHE_DIR / "coinbase"))
coinbase_svc = CoinbaseService(
    config.COINBASE_API_KEY_NAME, config.COINBASE_API_PRIVATE_KEY, cb_cache
)


# ---- Background uptime tracker ----

def _uptime_tracker():
    """Background thread: logs PowerPool worker uptime every 5 minutes,
    and runs alert checks after each poll cycle."""
    _time.sleep(30)  # Wait for app to start
    _cleanup_counter = 0
    while True:
        try:
            miners = inventory_mgr.get_all_miners()
            statuses = {}
            if powerpool_svc.is_configured():
                statuses = powerpool_svc.get_all_worker_statuses(miners)
                history_svc.record_uptime(statuses, miners)

            # ---- Alert checks ----
            try:
                if statuses:
                    alert_svc.check_miner_offline(statuses, miners)
                    alert_svc.check_hashrate_drop(statuses, miners)
                # Only check negative profit if cached data is fresh
                # (don't trigger new WhatToMine API calls from background)
                if engine._calc_cache and (
                    _time.time() - engine._calc_cache_time
                ) < 600:
                    miner_results = engine._calc_cache.get("miners", [])
                    if miner_results:
                        alert_svc.check_negative_profit(miner_results)
            except Exception as ae:
                logger.error("Alert check error: %s", ae)
        except Exception as e:
            logger.error("Uptime tracker error: %s", e)

        # Run DB cleanup once per day (~288 cycles of 5 min)
        _cleanup_counter += 1
        if _cleanup_counter >= 288:
            _cleanup_counter = 0
            try:
                history_svc.cleanup_old_data()
            except Exception as e:
                logger.error("DB cleanup error: %s", e)

        _time.sleep(300)  # 5 minutes


_tracker_thread = threading.Thread(target=_uptime_tracker, daemon=True)
_tracker_thread.start()


# ---- Auth routes ----

_login_attempts = {}  # IP -> (count, first_attempt_time)
_LOGIN_RATE_WINDOW = 300  # 5 minutes
_LOGIN_RATE_MAX = 10  # max attempts per window


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_enabled():
        return redirect("/")
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        now = _time.time()
        count, first_time = _login_attempts.get(ip, (0, now))
        if now - first_time > _LOGIN_RATE_WINDOW:
            count, first_time = 0, now
        if count >= _LOGIN_RATE_MAX:
            error = "Too many login attempts. Please wait a few minutes."
            return render_template("login.html", error=error)

        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if (
            username == config.DASHBOARD_USERNAME
            and bcrypt.checkpw(password.encode("utf-8"), _password_hash)
        ):
            _login_attempts.pop(ip, None)
            session["authenticated"] = True
            next_url = request.args.get("next", "/")
            return redirect(next_url)
        _login_attempts[ip] = (count + 1, first_time)
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


@app.route("/solar")
@login_required
def solar_page():
    return render_template("solar.html", active_page="solar")


# ---- Miners API ----

@app.route("/api/miners", methods=["GET"])
@login_required
def list_miners():
    return jsonify(inventory_mgr.get_all_miners())


def _validate_miner_data(data, require_all=False):
    """Validate miner fields. require_all=True for creation, False for updates."""
    errors = []
    if require_all:
        if not data.get("name", "").strip():
            errors.append("name is required")
        if not data.get("model", "").strip():
            errors.append("model is required")
        if not data.get("algorithm", "").strip():
            errors.append("algorithm is required")
    if "hashrate" in data or require_all:
        try:
            hashrate = float(data.get("hashrate", 0))
            if hashrate <= 0:
                errors.append("hashrate must be a positive number")
            data["hashrate"] = hashrate
        except (ValueError, TypeError):
            errors.append("hashrate must be a valid number")
    if "wattage" in data or require_all:
        try:
            wattage = float(data.get("wattage", 0))
            if wattage <= 0:
                errors.append("wattage must be a positive number")
            data["wattage"] = wattage
        except (ValueError, TypeError):
            errors.append("wattage must be a valid number")
    if "purchase_price" in data or require_all:
        try:
            pp = float(data.get("purchase_price", 0) or 0)
            if pp < 0:
                errors.append("purchase_price cannot be negative")
            data["purchase_price"] = pp
        except (ValueError, TypeError):
            errors.append("purchase_price must be a valid number")
    return errors


@app.route("/api/miners", methods=["POST"])
@login_required
def add_miner():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    errors = _validate_miner_data(data, require_all=True)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    miner = inventory_mgr.add_miner(data)
    engine.invalidate_cache()
    return jsonify(miner), 201


@app.route("/api/miners/<miner_id>", methods=["PUT"])
@login_required
def update_miner(miner_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    errors = _validate_miner_data(data, require_all=False)
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
    miner = inventory_mgr.update_miner(miner_id, data)
    if miner is None:
        return jsonify({"error": "Miner not found"}), 404
    engine.invalidate_cache()
    return jsonify(miner)


@app.route("/api/miners/<miner_id>", methods=["DELETE"])
@login_required
def delete_miner(miner_id):
    if inventory_mgr.delete_miner(miner_id):
        engine.invalidate_cache()
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
    errors = []
    if not data.get("name", "").strip():
        errors.append("name is required")
    elec_cost = data.get("electricity_cost_kwh")
    if elec_cost is None:
        errors.append("electricity_cost_kwh is required")
    else:
        try:
            elec_cost = float(elec_cost)
            if elec_cost < 0:
                errors.append("electricity_cost_kwh cannot be negative")
            data["electricity_cost_kwh"] = elec_cost
        except (ValueError, TypeError):
            errors.append("electricity_cost_kwh must be a valid number")
    if errors:
        return jsonify({"error": "Validation failed", "details": errors}), 400
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


@app.route("/api/pool-summary", methods=["GET"])
@login_required
def pool_summary():
    """Lightweight endpoint for the pool comparison page — returns only
    miner algorithm, revenue, quantity, pool, and status."""
    try:
        data = engine.calculate_all()
        miners = []
        for r in data.get("miners", []):
            m = r.get("miner", {})
            miners.append({
                "miner": {
                    "algorithm": m.get("algorithm", ""),
                    "pool": m.get("pool", ""),
                    "quantity": m.get("quantity", 1),
                    "name": m.get("name", ""),
                },
                "daily_revenue": r.get("daily_revenue", 0),
                "status": r.get("status", ""),
            })
        return jsonify({
            "miners": miners,
            "powerpool_configured": powerpool_svc.is_configured(),
        })
    except Exception as e:
        logger.error("Pool summary failed: %s", e)
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

    # Stale share analysis per miner
    stale_analysis = {}
    for miner_id, status in statuses.items():
        valid = status.get("valid_shares", 0) or 0
        stale = status.get("stale_shares", 0) or 0
        invalid = status.get("invalid_shares", 0) or 0
        total = valid + stale + invalid
        if total > 0:
            stale_pct = round(stale / total * 100, 2)
            invalid_pct = round(invalid / total * 100, 2)
            efficiency = round(valid / total * 100, 2)
            health = "good" if stale_pct < 1 else "warning" if stale_pct < 3 else "poor"
            stale_analysis[miner_id] = {
                "stale_pct": stale_pct,
                "invalid_pct": invalid_pct,
                "share_efficiency": efficiency,
                "health": health,
                "estimated_revenue_loss_pct": round(stale_pct + invalid_pct, 2),
            }

    return jsonify({
        "statuses": statuses,
        "stale_analysis": stale_analysis,
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
    pp_cache.invalidate_all()
    cb_cache.invalidate_all()
    engine.invalidate_cache()
    return jsonify({"success": True, "message": "All caches cleared"})


@app.route("/api/cache/refresh/<source>", methods=["POST"])
@login_required
def refresh_source_cache(source):
    caches = {
        "whattomine": wtm_cache,
        "hashrateno": hrn_cache,
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


# ---- Alert Settings & Log API ----

@app.route("/api/settings/alerts", methods=["GET"])
@login_required
def get_alert_settings():
    """Return all alert channel configurations."""
    return jsonify(alert_svc.get_config())


@app.route("/api/settings/alerts", methods=["PUT"])
@login_required
def save_alert_settings():
    """Save alert configuration for a channel."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    channel = data.get("channel")
    if channel not in ("telegram", "discord"):
        return jsonify({"error": "channel must be 'telegram' or 'discord'"}), 400
    settings = {
        "enabled": data.get("enabled", True),
        "alert_offline": data.get("alert_offline", True),
        "alert_hashrate_drop": data.get("alert_hashrate_drop", True),
        "alert_negative_profit": data.get("alert_negative_profit", True),
        "alert_daily_summary": data.get("alert_daily_summary", True),
        "hashrate_drop_pct": data.get("hashrate_drop_pct", 20.0),
    }
    alert_svc.save_config(
        channel,
        webhook_url=data.get("webhook_url"),
        bot_token=data.get("bot_token"),
        chat_id=data.get("chat_id"),
        settings=settings,
    )
    return jsonify({"success": True})


@app.route("/api/settings/alerts/test", methods=["POST"])
@login_required
def test_alert():
    """Send a test message to all enabled channels."""
    from datetime import datetime as _dt
    msg = (
        "\u2705 <b>Test Alert</b>\n"
        "Your mining dashboard alert integration is working!\n"
        f"Sent at: {_dt.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    sent = alert_svc.send_alert("test", msg)
    if sent:
        return jsonify({"success": True, "message": "Test alert sent"})
    return jsonify({"success": False, "message": "No enabled channels or send failed"}), 400


@app.route("/api/alerts/recent", methods=["GET"])
@login_required
def recent_alerts():
    """Return recent alert log entries."""
    limit = request.args.get("limit", 50, type=int)
    return jsonify(alert_svc.get_recent_alerts(limit=limit))


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

    # Sort by profit per watt descending, greedily pack into budget
    # (heuristic — may not be globally optimal for all combinations)
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
    """Fetch difficulty history and halving countdown from free APIs."""
    import requests as req

    algo = request.args.get("algo", "SHA-256")
    results = {}

    # Halving schedule data
    HALVING_DATA = {
        "SHA-256": {
            "coin": "BTC",
            "block_interval_seconds": 600,
            "halving_interval": 210000,
            "current_reward": 3.125,
            "post_halving_reward": 1.5625,
        },
        "Scrypt": {
            "coin": "LTC",
            "block_interval_seconds": 150,
            "halving_interval": 840000,
            "current_reward": 6.25,
            "post_halving_reward": 3.125,
        },
        "Equihash": {
            "coin": "ZEC",
            "block_interval_seconds": 75,
            "halving_interval": 1050000,
            "current_reward": 1.5625,
            "post_halving_reward": 0.78125,
        },
    }

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
            # Fetch current block height for halving countdown
            try:
                height_resp = req.get(
                    "https://blockchain.info/q/getblockcount", timeout=10
                )
                if height_resp.ok:
                    current_height = int(height_resp.text.strip())
                    halving_info = HALVING_DATA["SHA-256"]
                    next_halving_block = ((current_height // halving_info["halving_interval"]) + 1) * halving_info["halving_interval"]
                    blocks_remaining = next_halving_block - current_height
                    est_seconds = blocks_remaining * halving_info["block_interval_seconds"]
                    est_days = est_seconds / 86400
                    from datetime import datetime, timedelta, timezone
                    est_date = (datetime.now(timezone.utc) + timedelta(seconds=est_seconds)).strftime("%Y-%m-%d")
                    results["halving"] = {
                        "current_block": current_height,
                        "next_halving_block": next_halving_block,
                        "blocks_remaining": blocks_remaining,
                        "estimated_days": round(est_days, 1),
                        "estimated_date": est_date,
                        "current_reward": halving_info["current_reward"],
                        "post_halving_reward": halving_info["post_halving_reward"],
                        "reward_reduction_pct": 50,
                    }
            except Exception as e:
                logger.debug("BTC block height fetch failed: %s", e)

        elif algo == "Scrypt":
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
                results["data"] = []
                # Halving countdown from block height
                current_height = data.get("blocks")
                if current_height:
                    halving_info = HALVING_DATA["Scrypt"]
                    next_halving_block = ((current_height // halving_info["halving_interval"]) + 1) * halving_info["halving_interval"]
                    blocks_remaining = next_halving_block - current_height
                    est_seconds = blocks_remaining * halving_info["block_interval_seconds"]
                    est_days = est_seconds / 86400
                    from datetime import datetime, timedelta, timezone
                    est_date = (datetime.now(timezone.utc) + timedelta(seconds=est_seconds)).strftime("%Y-%m-%d")
                    results["halving"] = {
                        "current_block": current_height,
                        "next_halving_block": next_halving_block,
                        "blocks_remaining": blocks_remaining,
                        "estimated_days": round(est_days, 1),
                        "estimated_date": est_date,
                        "current_reward": halving_info["current_reward"],
                        "post_halving_reward": halving_info["post_halving_reward"],
                        "reward_reduction_pct": 50,
                    }

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
                current_height = data.get("blocks")
                if current_height:
                    halving_info = HALVING_DATA["Equihash"]
                    next_halving_block = ((current_height // halving_info["halving_interval"]) + 1) * halving_info["halving_interval"]
                    blocks_remaining = next_halving_block - current_height
                    est_seconds = blocks_remaining * halving_info["block_interval_seconds"]
                    est_days = est_seconds / 86400
                    from datetime import datetime, timedelta, timezone
                    est_date = (datetime.now(timezone.utc) + timedelta(seconds=est_seconds)).strftime("%Y-%m-%d")
                    results["halving"] = {
                        "current_block": current_height,
                        "next_halving_block": next_halving_block,
                        "blocks_remaining": blocks_remaining,
                        "estimated_days": round(est_days, 1),
                        "estimated_date": est_date,
                        "current_reward": halving_info["current_reward"],
                        "post_halving_reward": halving_info["post_halving_reward"],
                        "reward_reduction_pct": 50,
                    }
    except Exception as e:
        logger.error("Difficulty fetch error: %s", e)
        return jsonify({"error": str(e)}), 500

    return jsonify(results)


# ---- Electricity Dashboard Integration ----

ELECTRICITY_API = os.getenv("ELECTRICITY_API_URL", "http://127.0.0.1:5001")


@app.route("/api/electricity/solar-mining", methods=["GET"])
@login_required
def solar_mining_summary():
    """Fetch live solar + crypto data from the electricity dashboard."""
    import requests as req

    try:
        rt = req.get(f"{ELECTRICITY_API}/api/realtime", timeout=5).json()
        summary = req.get(f"{ELECTRICITY_API}/api/summary", timeout=5).json()
        roi = req.get(f"{ELECTRICITY_API}/api/solar/roi", timeout=5).json()

        # Realtime values (for live display)
        solar_w = rt.get("solar_production_w", 0)
        crypto_w = rt.get("crypto_mining_w", 0)
        consumption_w = rt.get("house_consumption_w", 0)
        net_grid_w = rt.get("net_grid_w", 0)

        energy_rate = summary.get("energy_rate", 0.08907)

        # Use actual daily accumulated kWh from electricity dashboard
        # (much more accurate than instantaneous watts × 24)
        actual_solar_kwh = summary.get("solar_kwh", 0)
        actual_consumption_kwh = summary.get("consumption_kwh", 0)
        actual_crypto_kwh = summary.get("crypto_kwh", 0)

        if actual_consumption_kwh > 0 and actual_solar_kwh > 0:
            # Crypto's proportional share of today's actual solar production
            crypto_share = min(actual_crypto_kwh / actual_consumption_kwh, 1.0)
            crypto_solar_kwh = min(actual_solar_kwh, actual_consumption_kwh) * crypto_share
            crypto_daily_cost = actual_crypto_kwh * energy_rate
            crypto_solar_savings_daily = crypto_solar_kwh * energy_rate
        else:
            # Fallback to instantaneous estimate if no daily data yet
            solar_offset_w = min(solar_w, consumption_w) if consumption_w > 0 else 0
            crypto_share_rt = crypto_w / consumption_w if consumption_w > 0 else 0
            crypto_solar_offset_w = solar_offset_w * crypto_share_rt
            crypto_daily_cost = (crypto_w / 1000) * 24 * energy_rate
            crypto_solar_savings_daily = (crypto_solar_offset_w / 1000) * 24 * energy_rate

        net_crypto_daily_cost = crypto_daily_cost - crypto_solar_savings_daily

        # Realtime solar offset for display
        solar_offset_pct = (solar_w / consumption_w * 100) if consumption_w > 0 else 0
        crypto_solar_offset_w = (
            min(solar_w, consumption_w) * (crypto_w / consumption_w)
            if consumption_w > 0 else 0
        )

        return jsonify({
            "connected": True,
            "realtime": {
                "solar_w": round(solar_w, 0),
                "crypto_w": round(crypto_w, 0),
                "consumption_w": round(consumption_w, 0),
                "net_grid_w": round(net_grid_w, 0),
                "solar_offset_pct": round(solar_offset_pct, 1),
                "crypto_solar_offset_w": round(crypto_solar_offset_w, 0),
            },
            "daily": {
                "crypto_cost_no_solar": round(crypto_daily_cost, 2),
                "crypto_solar_savings": round(crypto_solar_savings_daily, 2),
                "net_crypto_cost": round(net_crypto_daily_cost, 2),
            },
            "monthly": {
                "crypto_cost_no_solar": round(crypto_daily_cost * 30, 2),
                "crypto_solar_savings": round(crypto_solar_savings_daily * 30, 2),
                "net_crypto_cost": round(net_crypto_daily_cost * 30, 2),
            },
            "roi": {
                "payback_pct": roi.get("payback_pct", 0),
                "total_savings": roi.get("total_savings", 0),
                "net_cost": roi.get("net_cost", 0),
                "months_to_payback": roi.get("months_to_payback", 0),
            },
        })
    except Exception as e:
        logger.debug("Electricity dashboard not available: %s", e)
        return jsonify({"connected": False})


# ---- Solar Loan API ----

SOLAR_LOAN_FILE = os.path.join(config.DATA_DIR, "solar_loan.json")


def _load_solar_loan():
    try:
        with open(SOLAR_LOAN_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"loan": {}, "system": {}}


def _save_solar_loan(data):
    with open(SOLAR_LOAN_FILE, "w") as f:
        json.dump(data, f, indent=2)


@app.route("/api/solar-loan", methods=["GET"])
@login_required
def get_solar_loan():
    return jsonify(_load_solar_loan())


@app.route("/api/solar-loan", methods=["PUT"])
@login_required
def update_solar_loan():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    _save_solar_loan(data)
    return jsonify(data)


@app.route("/api/solar-loan/analysis", methods=["GET"])
@login_required
def solar_loan_analysis():
    """Full solar loan vs savings analysis pulling live data from electricity dashboard."""
    import requests as req
    from datetime import datetime

    loan_data = _load_solar_loan()
    loan = loan_data.get("loan", {})
    system = loan_data.get("system", {})

    monthly_payment = loan.get("monthly_payment", 0)

    # Fetch live solar data from electricity dashboard
    elec_connected = False
    solar_savings_daily = 0
    solar_production_kwh_today = 0
    energy_rate = 0.08907
    solar_roi = {}
    daily_summary = {}

    try:
        rt = req.get(f"{ELECTRICITY_API}/api/realtime", timeout=5).json()
        summary = req.get(f"{ELECTRICITY_API}/api/summary", timeout=5).json()
        roi_resp = req.get(f"{ELECTRICITY_API}/api/solar/roi", timeout=5).json()
        costs_resp = req.get(f"{ELECTRICITY_API}/api/costs?period=month", timeout=5).json()
        elec_connected = True

        energy_rate = summary.get("energy_rate", 0.08907)
        solar_roi = roi_resp
        solar_savings_daily = summary.get("solar_savings", 0) or 0
        solar_production_kwh_today = summary.get("solar_kwh", 0) or 0

        # Monthly savings from costs endpoint
        monthly_solar_savings = costs_resp.get("total_solar_savings", 0) or 0

        # If no monthly data yet, estimate from daily
        if monthly_solar_savings <= 0 and solar_savings_daily > 0:
            monthly_solar_savings = solar_savings_daily * 30

        daily_summary = {
            "solar_w": rt.get("solar_production_w", 0),
            "consumption_w": rt.get("house_consumption_w", 0),
            "crypto_w": rt.get("crypto_mining_w", 0),
            "net_grid_w": rt.get("net_grid_w", 0),
        }
    except Exception as e:
        logger.debug("Solar loan: electricity dashboard unavailable: %s", e)
        monthly_solar_savings = 0

    # Also pull demand charge savings estimate from profitability engine
    demand_savings_monthly = 0
    try:
        prof_data = engine.calculate_all()
        summary_data = prof_data.get("summary", {})
        demand_rate = summary_data.get("demand_rate", 0)
        # Estimate solar's demand reduction: if solar covers X% of consumption,
        # it reduces peak demand by roughly that percentage
        if demand_rate > 0 and daily_summary.get("consumption_w", 0) > 0:
            solar_w = daily_summary.get("solar_w", 0)
            cons_w = daily_summary.get("consumption_w", 0)
            solar_pct = min(solar_w / cons_w, 1.0) if cons_w > 0 else 0
            # Peak demand without solar ~ consumption / 1000 kW
            # Demand reduction from solar (rough estimate)
            peak_kw = cons_w / 1000
            demand_reduction_kw = peak_kw * solar_pct
            demand_savings_monthly = round(demand_reduction_kw * demand_rate, 2)
    except Exception as e:
        logger.debug("Solar loan: profitability engine error: %s", e)

    total_monthly_savings = monthly_solar_savings + demand_savings_monthly
    net_monthly = total_monthly_savings - monthly_payment
    annual_savings = total_monthly_savings * 12
    annual_cost = monthly_payment * 12

    # Remaining loan months estimate
    remaining_months = 0
    if monthly_payment > 0 and loan.get("outstanding_principal", 0) > 0:
        remaining_months = int(loan["outstanding_principal"] / monthly_payment)

    # Post-loan monthly benefit (after loan is paid off)
    post_loan_monthly = total_monthly_savings

    # Lifetime value: savings after loan payoff * estimated panel life remaining
    panel_life_years = 25
    install_date = system.get("install_date", "")
    years_active = 0
    if install_date:
        try:
            start = datetime.strptime(install_date, "%Y-%m-%d")
            years_active = (datetime.now() - start).days / 365.25
        except ValueError:
            pass
    remaining_life_years = max(panel_life_years - years_active, 0)
    remaining_loan_years = remaining_months / 12

    # Total value over remaining panel life
    if remaining_loan_years > 0:
        value_during_loan = net_monthly * 12 * min(remaining_loan_years, remaining_life_years)
        value_after_loan = post_loan_monthly * 12 * max(remaining_life_years - remaining_loan_years, 0)
        lifetime_value = value_during_loan + value_after_loan
    else:
        lifetime_value = post_loan_monthly * 12 * remaining_life_years

    return jsonify({
        "electricity_connected": elec_connected,
        "loan": loan,
        "system": system,
        "energy_rate": energy_rate,
        "realtime": daily_summary,
        "monthly": {
            "payment": monthly_payment,
            "energy_savings": round(monthly_solar_savings, 2),
            "demand_savings": demand_savings_monthly,
            "total_savings": round(total_monthly_savings, 2),
            "net": round(net_monthly, 2),
            "is_profitable": net_monthly >= 0,
        },
        "annual": {
            "cost": round(annual_cost, 2),
            "savings": round(annual_savings, 2),
            "net": round(annual_savings - annual_cost, 2),
        },
        "loan_progress": {
            "remaining_months": remaining_months,
            "remaining_years": round(remaining_months / 12, 1) if remaining_months > 0 else 0,
            "outstanding": loan.get("outstanding_principal", 0),
        },
        "post_loan": {
            "monthly_benefit": round(post_loan_monthly, 2),
            "annual_benefit": round(post_loan_monthly * 12, 2),
        },
        "lifetime": {
            "panel_life_years": panel_life_years,
            "years_active": round(years_active, 1),
            "remaining_life_years": round(remaining_life_years, 1),
            "total_value": round(lifetime_value, 2),
        },
        "solar_roi": {
            "payback_pct": solar_roi.get("payback_pct", 0),
            "total_savings_to_date": solar_roi.get("total_savings", 0),
            "avg_monthly_savings": solar_roi.get("avg_monthly_savings", 0),
            "avg_monthly_kwh": solar_roi.get("avg_monthly_kwh", 0),
        },
    })


# ---- PDU / Auto-Pause API ----

@app.route("/api/pdu/config/<miner_id>", methods=["GET"])
@login_required
def get_pdu_config(miner_id):
    cfg = pdu_svc.get_config(miner_id)
    return jsonify(cfg or {})


@app.route("/api/pdu/config/<miner_id>", methods=["PUT"])
@login_required
def save_pdu_config(miner_id):
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400
    result = pdu_svc.save_config(miner_id, data)
    return jsonify(result)


@app.route("/api/pdu/status", methods=["GET"])
@login_required
def pdu_status():
    return jsonify(pdu_svc.get_pause_status())


@app.route("/api/pdu/check", methods=["POST"])
@login_required
def pdu_check():
    """Manually trigger auto-pause check against current profitability."""
    try:
        data = engine.calculate_all()
        actions = pdu_svc.check_and_autopause(data.get("miners", []))
        return jsonify({"actions": actions})
    except Exception as e:
        logger.error("PDU check failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/pdu/log", methods=["GET"])
@login_required
def pdu_log():
    miner_id = request.args.get("miner_id")
    limit = request.args.get("limit", 50, type=int)
    return jsonify(pdu_svc.get_log(miner_id=miner_id, limit=limit))


@app.route("/api/pdu/power/<miner_id>/<action>", methods=["POST"])
@login_required
def pdu_manual_power(miner_id, action):
    """Manually power on/off a miner's PDU outlet."""
    if action not in ("on", "off"):
        return jsonify({"error": "Action must be 'on' or 'off'"}), 400
    cfg = pdu_svc.get_config(miner_id)
    if not cfg:
        return jsonify({"error": "No PDU config for this miner"}), 404
    if action == "on":
        success = pdu_svc.power_on(cfg)
    else:
        success = pdu_svc.power_off(cfg)
    return jsonify({"success": success, "action": action})


# ---- Tax Export API ----

@app.route("/api/export/tax/csv", methods=["GET"])
@login_required
def export_tax_csv():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "start and end date parameters required (YYYY-MM-DD)"}), 400
    try:
        csv_data = tax_export_svc.generate_csv(start, end)
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename=mining_tax_report_{start}_to_{end}.csv"},
        )
    except Exception as e:
        logger.error("CSV export failed: %s", e)
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/tax/pdf", methods=["GET"])
@login_required
def export_tax_pdf():
    start = request.args.get("start")
    end = request.args.get("end")
    if not start or not end:
        return jsonify({"error": "start and end date parameters required (YYYY-MM-DD)"}), 400
    try:
        pdf_data = tax_export_svc.generate_pdf(start, end)
        return Response(
            pdf_data,
            mimetype="application/pdf",
            headers={"Content-Disposition": f"attachment; filename=mining_tax_report_{start}_to_{end}.pdf"},
        )
    except Exception as e:
        logger.error("PDF export failed: %s", e)
        return jsonify({"error": str(e)}), 500


# ---- TOU (Time-of-Use) API ----

@app.route("/api/locations/<location_id>/tou", methods=["GET"])
@login_required
def get_tou_schedule(location_id):
    schedules = tou_svc.get_schedules(location_id)
    current_rate = tou_svc.get_current_rate(location_id)
    weighted_rate = tou_svc.get_weighted_daily_rate(location_id)
    return jsonify({
        "location_id": location_id,
        "schedules": schedules,
        "current_rate": current_rate,
        "weighted_daily_rate": weighted_rate,
    })


@app.route("/api/locations/<location_id>/tou", methods=["PUT"])
@login_required
def save_tou_schedule(location_id):
    data = request.get_json()
    if not data or "periods" not in data:
        return jsonify({"error": "periods array required"}), 400
    schedules = tou_svc.save_schedules(location_id, data["periods"])
    engine.invalidate_cache()  # TOU rates affect profitability
    return jsonify({"schedules": schedules})


@app.route("/api/locations/<location_id>/tou", methods=["DELETE"])
@login_required
def delete_tou_schedule(location_id):
    deleted = tou_svc.delete_schedules(location_id)
    engine.invalidate_cache()
    return jsonify({"success": deleted})


# ---- ROI Tracking API ----

@app.route("/api/miners/<miner_id>/roi-history", methods=["GET"])
@login_required
def miner_roi_history(miner_id):
    """Get cumulative earnings, electricity, and breakeven progress for a miner."""
    miner = inventory_mgr.get_miner(miner_id)
    if not miner:
        return jsonify({"error": "Miner not found"}), 404

    conn = history_svc._get_conn()

    # Get cumulative totals from profit_snapshots
    row = conn.execute(
        """SELECT
            COUNT(DISTINCT DATE(timestamp)) as days_mining,
            SUM(daily_revenue) / COUNT(*) * COUNT(DISTINCT DATE(timestamp)) as total_revenue,
            SUM(daily_electricity) / COUNT(*) * COUNT(DISTINCT DATE(timestamp)) as total_electricity,
            SUM(daily_profit) / COUNT(*) * COUNT(DISTINCT DATE(timestamp)) as total_profit,
            AVG(daily_profit) as avg_daily_profit,
            MIN(DATE(timestamp)) as first_seen,
            MAX(DATE(timestamp)) as last_seen
           FROM profit_snapshots
           WHERE miner_id = ?""",
        (miner_id,),
    ).fetchone()
    conn.close()

    if not row or not row["days_mining"]:
        return jsonify({
            "miner_id": miner_id,
            "hardware_cost": miner.get("purchase_price", 0),
            "total_earned_to_date": 0,
            "total_electricity_to_date": 0,
            "net_profit_to_date": 0,
            "days_mining": 0,
            "breakeven_progress_pct": 0,
            "projected_breakeven_date": None,
        })

    hardware_cost = miner.get("purchase_price", 0) * miner.get("quantity", 1)
    total_earned = row["total_revenue"] or 0
    total_electricity = row["total_electricity"] or 0
    net_profit = row["total_profit"] or 0
    days_mining = row["days_mining"]
    avg_daily = row["avg_daily_profit"] or 0

    if hardware_cost > 0:
        breakeven_pct = round(net_profit / hardware_cost * 100, 1)
    else:
        breakeven_pct = None  # N/A

    projected_date = None
    if hardware_cost > 0 and avg_daily > 0:
        remaining = hardware_cost - net_profit
        if remaining > 0:
            from datetime import datetime, timedelta
            days_left = remaining / avg_daily
            projected_date = (datetime.now() + timedelta(days=days_left)).strftime("%Y-%m-%d")
        else:
            projected_date = "Already reached"

    return jsonify({
        "miner_id": miner_id,
        "hardware_cost": hardware_cost,
        "total_earned_to_date": round(total_earned, 2),
        "total_electricity_to_date": round(total_electricity, 2),
        "net_profit_to_date": round(net_profit, 2),
        "days_mining": days_mining,
        "avg_daily_profit": round(avg_daily, 2),
        "breakeven_progress_pct": breakeven_pct,
        "projected_breakeven_date": projected_date,
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
    })


if __name__ == "__main__":
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=config.FLASK_DEBUG,
    )
