"""Fleet settings from environment variables.

Write-capable keys (Foreman, Clore) belong in ``fleet.env``, which only the poller
container loads, so the web container never holds them.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
load_dotenv("fleet.env")

BASE_DIR = Path(__file__).resolve().parent.parent
TELEMETRY_DB = Path(os.getenv("FLEET_DB", str(BASE_DIR / "data" / "telemetry.db")))

# Raw samples older than this are purged (rollups come later)
SAMPLE_RETENTION_DAYS = int(os.getenv("FLEET_SAMPLE_RETENTION_DAYS", "90"))

# PowerPool observer key (read-only by design)
POWERPOOL_OBSERVER_KEY = os.getenv("POWERPOOL_OBSERVER_KEY", "")

# Foreman (Metered Mining hosted miners). The key can also change miner settings.
FOREMAN_API_KEY = os.getenv("FOREMAN_API_KEY", "")
FOREMAN_CLIENT_ID = os.getenv("FOREMAN_CLIENT_ID", "")

# Clore.ai host account. The key can also reprice servers and spend balance.
CLORE_API_KEY = os.getenv("CLORE_API_KEY", "")

# Kryptex pool, wallet-keyed and public: "coin:address,coin:address"
KRYPTEX_WALLETS = [
    tuple(item.strip().split(":", 1))
    for item in os.getenv("KRYPTEX_WALLETS", "").split(",")
    if ":" in item
]

# Home ASICs read over the LAN (cgminer API, TCP 4028): "name=ip,name=ip[:port]"
LAN_MINERS = [
    tuple(item.strip().split("=", 1))
    for item in os.getenv("LAN_MINERS", "").split(",")
    if "=" in item
]

# Electricity dashboard (home marginal rate and demand)
ELECTRICITY_API_URL = os.getenv("ELECTRICITY_API_URL", "http://127.0.0.1:5001")
