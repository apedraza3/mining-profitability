import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# Paths
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / "cache"
INVENTORY_FILE = DATA_DIR / "inventory.json"
LOCATIONS_FILE = DATA_DIR / "locations.json"
COIN_MAPPINGS_FILE = DATA_DIR / "coin_mappings.json"

# Cache TTLs (seconds)
WHATTOMINE_CACHE_TTL = 1800       # 30 minutes
HASHRATENO_CACHE_TTL = 86400      # 24 hours
MININGNOW_CACHE_TTL = 21600       # 6 hours

# API Keys
HASHRATE_NO_API_KEY = os.getenv("HASHRATE_NO_API_KEY", "")

# PowerPool
POWERPOOL_OBSERVER_KEY = os.getenv("POWERPOOL_OBSERVER_KEY", "")

# Coinbase (CDP API key)
COINBASE_API_KEY_NAME = os.getenv("COINBASE_API_KEY_NAME", "")
COINBASE_API_PRIVATE_KEY = os.getenv("COINBASE_API_PRIVATE_KEY", "")
COINBASE_CACHE_TTL = 300  # 5 minutes

# WhatToMine
WHATTOMINE_BASE_URL = "https://whattomine.com"
WHATTOMINE_REQUEST_DELAY = 3.0    # seconds between requests (be polite)

# Hashrate.no
HASHRATENO_BASE_URL = "https://hashrate.no/api/v2"

# MiningNow
MININGNOW_BASE_URL = "https://miningnow.com"
MININGNOW_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# Hashrate.no reference hashrates per model slug.
# Used to scale HR.no revenue to the user's actual hashrate.
# Format: slug -> (hashrate_value, hashrate_unit)
HASHRATENO_REFERENCE_SPECS = {
    # SHA-256 (TH/s)
    "s21": (200, "TH/s"),
    "s21hydro": (335, "TH/s"),
    "s21phydro": (319, "TH/s"),
    "s21plus": (216, "TH/s"),
    "s21pro": (234, "TH/s"),
    "s21xp": (270, "TH/s"),
    "s21xphydro": (473, "TH/s"),
    "s21immersion": (302, "TH/s"),
    "a2pro": (255, "TH/s"),
    "a2prohydro": (500, "TH/s"),
    # Scrypt (GH/s)
    "l9": (16, "GH/s"),
    "l11": (18.7, "GH/s"),
    "l11pro": (19.5, "GH/s"),
    "dg1": (11, "GH/s"),
    "dg1plus": (14, "GH/s"),
    "dg1lite": (11, "GH/s"),
    "dg2": (16, "GH/s"),
    "dg2plus": (20.5, "GH/s"),
    "l1": (5.3, "GH/s"),
    # Equihash (KSol/s)
    "z11": (135, "KH/s"),
    "z15": (420, "KH/s"),
}

# Profitability thresholds (daily profit per unit in USD)
PROFITABLE_THRESHOLD = 1.00       # >= $1/day = green
MARGINAL_THRESHOLD = 0.00         # >= $0/day = yellow, < $0/day = red

# Auth
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")  # blank = auth disabled

# Flask
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
