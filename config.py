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

# Profitability thresholds (daily profit per unit in USD)
PROFITABLE_THRESHOLD = 1.00       # >= $1/day = green
MARGINAL_THRESHOLD = 0.00         # >= $0/day = yellow, < $0/day = red

# Flask
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"
