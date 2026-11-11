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
# Owner-edited: support loads on the metered crypto circuits and which PDU circuit feeds
# which machines. See data/fleet_overheads.example.json:
# {"pdu_circuits": {"apc:Bank 1": ["Z11-1"]},
#  "home": [{"label": "Immersion pump + dry cooler", "serves": ["Z15", "Z11-1", "Z11-2"], "max_w": 2500,
#            "pdu": "chatsworth:CB1"}, {"label": "Server + workshop", "mining": false}]}
FLEET_OVERHEADS = Path(os.getenv("FLEET_OVERHEADS", str(BASE_DIR / "data" / "fleet_overheads.json")))
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

# Home Assistant (basement climate + AC Infinity fans), read-only long-lived token
HA_URL = os.getenv("HA_URL", "http://127.0.0.1:8123")
HA_TOKEN = os.getenv("HA_TOKEN", "")
HA_PREFIX = os.getenv("HA_PREFIX", "new_device")
HA_PROBE_LABEL = os.getenv("HA_PROBE_LABEL", "Hot aisle")   # where the controller's probe hangs
HA_FAN_PORTS = os.getenv("HA_FAN_PORTS", "1:S8 exhaust (port 1),2:S8 exhaust (port 2)")

# GPU temps over SSH, read-only forced command (see fleet/adapters/gpu_temps.py):
# name=user@host:format,...  format nvidia | hiveos
GPU_TEMP_HOSTS = os.getenv("GPU_TEMP_HOSTS", "")

# Exhaust fan control from room + machine temps (see fleet/fan_control.py)
FAN_CONTROL = os.getenv("FAN_CONTROL", "off")        # off | dry_run | on
FAN_MIN = int(os.getenv("FAN_MIN", "4"))             # never slower than this (0-10)
FAN_MAX = int(os.getenv("FAN_MAX", "10"))
FAN_AIR_F = os.getenv("FAN_AIR_F", "80,95")          # hot-aisle F: min speed at 80, max at 95
FAN_ASIC_C = os.getenv("FAN_ASIC_C", "70,85")        # ASIC chip C
FAN_GPU_C = os.getenv("FAN_GPU_C", "70,83")          # GPU core C
FAN_GPU_MEM_C = os.getenv("FAN_GPU_MEM_C", "85,98")  # GPU memory (HBM/GDDR) C
FAN_GPU_HOT_C = os.getenv("FAN_GPU_HOT_C", "90,105")  # GPU hotspot/junction C (HiveOS jtemp)
FAN_EXCLUDE = os.getenv("FAN_EXCLUDE", "Z15,Z11-1,Z11-2")  # immersion-cooled (C6 tank): room fans don't cool them
FAN_STALE_S = int(os.getenv("FAN_STALE_S", "600"))
FAN_DOWN_HOLD_S = int(os.getenv("FAN_DOWN_HOLD_S", "600"))

# Vast.ai host account. Create it restricted to machine_read + billing_read.
VAST_API_KEY = os.getenv("VAST_API_KEY", "")

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
