# Mining Profitability Dashboard

A self-hosted, real-time cryptocurrency mining profitability tracker that aggregates data from multiple sources to give miners a unified view of their operation's performance, costs, and ROI.

Built to solve a real problem: managing a fleet of ASIC miners across multiple locations with different electricity rates, solar offsets, and hosting providers — without juggling spreadsheets and browser tabs.

**The only actively maintained, self-hosted, Docker-native mining dashboard in the open source space.**

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Flask](https://img.shields.io/badge/Flask-3.1-green)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![License](https://img.shields.io/badge/License-MIT-yellow)

## Why This Exists

Every major mining monitor is either cloud-dependent (Hive OS, minerstat, Braiins), Windows-centric (Awesome Miner), marketplace-locked (NiceHash), enterprise-only (Foreman), or abandoned open source. This project fills the gap for technically capable homelab miners who want:

- **Real electricity costs** from actual smart meter data (not estimates)
- **Solar integration** — nobody else does this
- **Self-hosted + Docker** — one command deploy, your data never leaves your network
- **Accurate profitability** — pool fees, demand charges, solar offset, hosting fees all factored in
- **No subscriptions** — free forever

## Features

### Dashboard
- **Fleet summary** — daily/monthly profit, total investment, ROI timeline, fleet health at a glance
- **Per-miner breakdown** — sortable table with revenue, electricity cost, expected vs actual profit, and status per unit
- **Location breakdown** — profit aggregated by mining site with per-location electricity rates and solar offset
- **Profit history** — 7/30/90-day trend charts built with Chart.js
- **Coin switch alerts** — notifies when a more profitable coin is available for your algorithm
- **Auto-refresh** — optional 30-minute polling to keep numbers current

### Telegram & Discord Alerts
- **Miner offline** — instant notification when a worker drops off the pool
- **Hashrate drop** — alert when hashrate drops below configurable threshold (default 20%)
- **Negative profit** — alert when a miner becomes unprofitable
- **Daily summary** — automated daily P&L report with top performers
- Configurable per-channel with test message support

### Smart PDU Auto-Pause (Strike Price)
Automatically power off miners when profit goes negative, resume when profitable:
- **Tasmota** smart plugs — HTTP API control
- **TP-Link Kasa** — smart plug integration
- **Generic REST** — configurable URL template for any PDU with an API
- Configurable pause threshold, resume threshold, and cooldown period per miner

### Tax Export
- **CSV export** — mining income report with date, miner, algorithm, revenue, electricity cost, net profit
- **PDF export** — formatted tax report with summary table and daily breakdown
- Date range picker with quick presets (YTD, last year, 30/90 days)

### Time-of-Use Electricity Rates
- Support peak/off-peak/super-off-peak rate schedules per location
- Automatic weighted daily average for profitability calculations
- Handles overnight spans (e.g., off-peak 10 PM - 6 AM)

### ROI & Breakeven Tracker
- Per-miner: total earned to date, total electricity paid, net profit, breakeven progress
- Uses actual historical data from profit snapshots (not projections)
- Progress bar with projected breakeven date

### Multi-Source Data Aggregation
Instead of trusting a single source, profitability is calculated from multiple independent sources:

| Source | Method | Data |
|--------|--------|------|
| **WhatToMine** | Public API (throttled) | Coin profitability, difficulty, nethash |
| **Hashrate.no** | Authenticated API | ASIC/GPU model-specific estimates |

Each source is independently cached with configurable TTLs and graceful fallback if unavailable.

### Analysis Tools
- **Swap Calculator** — compare your current miner vs a replacement: profit delta, breakeven days, monthly savings, profit-per-watt
- **Power Optimizer** — greedy knapsack algorithm that selects the most profitable miner subset within a wattage budget
- **Pool Comparison** — curated fee database for 10-15 pools per algorithm (SHA-256, Scrypt, Equihash, KHeavyHash, Etchash)
- **Difficulty Trends** — 180-day historical difficulty charts for BTC, LTC, ZEC from public blockchain APIs

### Integrations
- **PowerPool** — live worker monitoring matched to your inventory (online/offline status, hashrate, shares, efficiency)
- **Solar Integration** — connects to a companion electricity monitoring dashboard to calculate real solar offset savings from actual metered production
- **Smart Meter Integration** — actual electricity costs from Emporia Vue / SunPower PVS6 data
- **CSV Power Import** — upload power consumption reports (Foreman-compatible) for actual vs rated wattage tracking

### Solar + Mining Economics
For miners with solar panels, the dashboard calculates:
- Effective electricity rate after solar offset
- Daily/monthly solar savings (from actual metered production, not estimates)
- Demand charge impact (peak kW tracking with high-water mark per billing cycle)
- Solar loan ROI analysis — net monthly savings vs loan payment, lifetime value projection

## Profitability Calculation

The engine calculates per-miner profitability with all costs factored in:

```
daily_revenue    = best_of(whattomine, hashrateno) × (actual_hashrate / rated_hashrate)
pool_fee         = daily_revenue × pool_fee_pct
net_revenue      = daily_revenue - pool_fee
daily_electricity = (wattage / 1000) × 24 × electricity_rate × (1 - solar_offset)
hosting_fee      = monthly_hosting_fee / 30
daily_profit     = net_revenue - daily_electricity - hosting_fee

# Summary includes demand charge
total_daily_profit = sum(miner_profits) - (demand_charge / 30)
```

Status thresholds:
- **Profitable** (green): >= $1.00/day
- **Marginal** (yellow): $0.00 - $0.99/day
- **Unprofitable** (red): < $0.00/day

## Quick Start

### Docker (recommended)

```bash
git clone https://github.com/apedraza3/mining-profitability.git
cd mining-profitability

# Configure environment
cp .env.example .env
# Edit .env with your API keys (see Configuration below)

# Set up initial data files
cp data/inventory.example.json data/inventory.json
cp data/locations.example.json data/locations.json

# Build and run
docker compose up -d

# Access at http://localhost:5000
```

### Local Development

```bash
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

cp .env.example .env
cp data/inventory.example.json data/inventory.json
cp data/locations.example.json data/locations.json

python app.py
```

## Configuration

### Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `HASHRATE_NO_API_KEY` | Optional | API key from [hashrate.no/account](https://hashrate.no/account). Enables second profitability source |
| `POWERPOOL_OBSERVER_KEY` | Optional | Observer key from PowerPool dashboard. Enables live worker monitoring |
| `DASHBOARD_USERNAME` | Optional | Login username (default: `admin`) |
| `DASHBOARD_PASSWORD` | Optional | Login password. **Leave blank to disable auth** |
| `ELECTRICITY_API_URL` | Optional | URL of companion electricity dashboard for solar/demand data |
| `FLASK_HOST` | Optional | Bind address (default: `127.0.0.1`) |
| `FLASK_PORT` | Optional | Port (default: `5000`) |
| `FLASK_DEBUG` | Optional | Debug mode (default: `false`) |
| `COINBASE_API_KEY_NAME` | Optional | CDP API key name from Coinbase Developer Platform |
| `COINBASE_API_PRIVATE_KEY` | Optional | CDP API private key (ES256) |

### Adding Miners

Miners can be added through the UI (+ Add Miner button) or by editing `data/inventory.json`:

```json
{
  "name": "My-S21",
  "model": "Antminer S21",
  "type": "ASIC",
  "algorithm": "SHA-256",
  "hashrate": 200,
  "hashrate_unit": "TH/s",
  "wattage": 3500,
  "location_id": "location-1",
  "quantity": 2,
  "purchase_price": 5000,
  "pool_fee_pct": 1.0,
  "status": "active"
}
```

### Adding Locations

```json
{
  "id": "location-1",
  "name": "garage",
  "electricity_cost_kwh": 0.10,
  "hosting_fee_monthly": 0,
  "currency": "USD"
}
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Flask App (app.py)                 │
│              50+ REST API endpoints                  │
├─────────────────────────────────────────────────────┤
│                  Services Layer                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
│  │ Profitability │ │  Inventory   │ │   History    │ │
│  │    Engine     │ │   Manager    │ │   Service    │ │
│  └──────┬───────┘ └──────────────┘ └──────┬───────┘ │
│         │                                  │         │
│  ┌──────┴──────────────────────────┐ ┌────┴───────┐ │
│  │      Data Source Services       │ │  New Svcs  │ │
│  │  WhatToMine  Hashrate.no       │ │  Alerts    │ │
│  │  PowerPool   PowerImport       │ │  PDU       │ │
│  └──────┬──────────────────────────┘ │  TOU       │ │
│         │                            │  TaxExport │ │
│  ┌──────┴───────┐                    └────────────┘ │
│  │ CacheManager │  TTL-based JSON file cache        │
│  └──────────────┘  per service, per endpoint        │
├─────────────────────────────────────────────────────┤
│  Frontend: Vanilla JS + Chart.js + CSS Variables    │
│  Dark theme, responsive, no build step required     │
└─────────────────────────────────────────────────────┘
         │
    External APIs
    ├── whattomine.com (public, throttled 3s delay)
    ├── hashrate.no (API key, 24hr cache)
    ├── api.powerpool.io (observer key, 2min cache)
    ├── Electricity Dashboard API (local, solar/demand)
    └── Telegram / Discord (alert webhooks)
```

## Project Structure

```
mining-profitability/
├── app.py                      # Flask app, 50+ routes, background threads
├── config.py                   # Environment config, constants, TTLs
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── services/
│   ├── profitability_engine.py # Core calculation engine
│   ├── whattomine_service.py   # WhatToMine API client
│   ├── hashrateno_service.py   # Hashrate.no API + fuzzy matching
│   ├── powerpool_service.py    # PowerPool worker monitoring
│   ├── alert_service.py        # Telegram/Discord notifications
│   ├── pdu_service.py          # Smart PDU power control
│   ├── tou_service.py          # Time-of-use rate schedules
│   ├── tax_export_service.py   # CSV/PDF tax report generation
│   ├── coinbase_service.py     # Coinbase CDP wallet API
│   ├── inventory_manager.py    # Thread-safe JSON CRUD
│   ├── history_service.py      # SQLite (11 tables) time-series + config
│   ├── cache_manager.py        # TTL file cache
│   └── power_import.py         # CSV power data parser
├── templates/                  # Jinja2 HTML templates
│   ├── base.html               # Nav + layout
│   ├── index.html              # Main dashboard
│   ├── swap.html               # Swap calculator
│   ├── pools.html              # Pool comparison
│   ├── optimizer.html          # Power optimizer
│   ├── difficulty.html         # Difficulty trends
│   └── solar.html              # Solar loan analysis
├── static/
│   ├── css/dashboard.css       # Dark theme, CSS variables
│   └── js/
│       ├── dashboard.js        # Main state + rendering
│       ├── alerts.js           # Alert settings UI
│       ├── analysis-tools.js   # Swap, optimizer, pool logic
│       ├── charts.js           # Chart.js profit history
│       ├── inventory-modal.js  # Add/edit miner modals
│       └── solar.js            # Solar loan calculations
├── data/                       # User data (gitignored)
│   ├── inventory.json          # Miner definitions
│   ├── locations.json          # Mining locations
│   └── coin_mappings.json      # Algorithm -> coin priorities
└── cache/                      # API response cache (gitignored)
```

## Background Processes

Three daemon threads run alongside the Flask server:

- **Uptime Tracker** — polls PowerPool every 5 minutes, records miner online/offline status and hashrate to SQLite, triggers alert checks
- **Profit Snapshots** — records daily profit per miner (throttled to once per hour)
- **Auto-Pause Monitor** — checks miner profitability against strike price thresholds, controls smart PDU outlets

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.13, Flask 3.1 |
| Database | SQLite (WAL mode, 11 tables) for history/config; JSON files for inventory |
| Frontend | Vanilla JS, Chart.js 4.4, CSS3 variables |
| Auth | bcrypt password hashing, Flask sessions |
| PDF Export | fpdf2 |
| Matching | rapidfuzz (fuzzy string matching for API model lookup) |
| Deployment | Docker, Docker Compose |
| Caching | File-based JSON with configurable TTL per source |
| Alerts | Telegram Bot API, Discord Webhooks |

## Contributing

Contributions welcome. Areas that would be particularly valuable:

- **Additional pool integrations** (ViaBTC, F2Pool, Foundry)
- **GPU miner support** (temperature/fan monitoring via CGMiner API)
- **Additional smart plug support** (Shelly, Sonoff, etc.)
- **Mobile app** (React Native dashboard companion)
- **Grafana export** (Prometheus metrics endpoint)

## License

MIT
