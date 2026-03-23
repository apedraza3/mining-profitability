# Mining Profitability Dashboard

A real-time cryptocurrency mining profitability tracker that aggregates data from multiple sources to give miners a unified view of their operation's performance, costs, and ROI.

Built to solve a real problem: managing a fleet of ASIC miners across multiple locations with different electricity rates, solar offsets, and hosting providers — without juggling spreadsheets and browser tabs.

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Flask](https://img.shields.io/badge/Flask-3.1-green)
![Docker](https://img.shields.io/badge/Docker-Compose-blue)
![License](https://img.shields.io/badge/License-MIT-yellow)

## Features

### Dashboard
- **Fleet summary** — daily/monthly profit, total investment, ROI timeline, fleet health at a glance
- **Per-miner breakdown** — sortable table with revenue, electricity cost, profit, and status per unit
- **Location breakdown** — profit aggregated by mining site with per-location electricity rates
- **Profit history** — 7/30/90-day trend charts built with Chart.js
- **Coin switch alerts** — notifies when a more profitable coin is available for your algorithm
- **Auto-refresh** — optional 30-minute polling to keep numbers current

### Multi-Source Data Aggregation
The core differentiator. Instead of trusting a single source, profitability is calculated from up to three independent sources, and the best estimate is used:

| Source | Method | Data |
|--------|--------|------|
| **WhatToMine** | Public API (throttled) | Coin profitability, difficulty, nethash |
| **Hashrate.no** | Authenticated API | ASIC/GPU model-specific estimates |
| **MiningNow** | Web scraping (3 fallback strategies) | ASIC rankings and specs |

Each source is independently cached with configurable TTLs and graceful fallback if unavailable.

### Analysis Tools

- **Swap Calculator** — compare your current miner vs a replacement: profit delta, breakeven days, monthly savings, profit-per-watt
- **Power Optimizer** — greedy knapsack algorithm that selects the most profitable miner subset within a wattage budget
- **Pool Comparison** — curated fee database for 10-15 pools per algorithm (SHA-256, Scrypt, Equihash, KHeavyHash, Etchash)
- **Difficulty Trends** — 180-day historical difficulty charts for BTC, LTC, ZEC from public blockchain APIs

### Integrations

- **PowerPool** — live worker monitoring matched to your inventory (online/offline status, hashrate, shares, earnings)
- **Coinbase Wallet** — portfolio balance and holdings via CDP API (JWT/ES256 auth)
- **Solar Integration** — connects to a companion electricity monitoring dashboard to calculate real solar offset savings on your electricity costs
- **CSV Power Import** — upload power consumption reports (Foreman-compatible) for actual vs rated wattage tracking

### Solar + Mining Economics
For miners with solar panels, the dashboard calculates:
- Effective electricity rate after solar offset
- Daily/monthly solar savings (from actual metered production, not estimates)
- Demand charge impact (peak kW tracking with high-water mark per billing cycle)
- Solar loan ROI analysis — net monthly savings vs loan payment, lifetime value projection

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Flask App (app.py)                 │
│              35+ REST API endpoints                  │
├─────────────────────────────────────────────────────┤
│                  Services Layer                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ │
│  │ Profitability │ │  Inventory   │ │   History    │ │
│  │    Engine     │ │   Manager    │ │   Service    │ │
│  └──────┬───────┘ └──────────────┘ └──────────────┘ │
│         │                                            │
│  ┌──────┴────────────────────────────────┐          │
│  │         Data Source Services           │          │
│  │  WhatToMine  Hashrate.no  MiningNow   │          │
│  │  PowerPool   Coinbase     PowerImport │          │
│  └──────┬────────────────────────────────┘          │
│         │                                            │
│  ┌──────┴───────┐                                   │
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
    ├── miningnow.com (scraped, 6hr cache)
    ├── api.powerpool.io (observer key, 2min cache)
    ├── api.coinbase.com (CDP JWT, 5min cache)
    ├── blockchain.com (public, on-demand)
    └── Electricity Dashboard API (local, on-demand)
```

### Key Design Decisions

- **File-based JSON storage** for inventory/locations — no database setup required, easy to inspect and version control
- **SQLite** only for time-series data (profit snapshots, uptime logs) where append performance matters
- **Multi-strategy scraping** for MiningNow — tries Next.js `__NEXT_DATA__`, then `__next_f.push()` chunks, then HTML parsing as fallback
- **Fuzzy model matching** with rapidfuzz (weighted token_set + partial + ratio scoring) for mapping user miner names to API model identifiers
- **Reference-based scaling** for Hashrate.no — fetches profitability once per model at reference hashrate, then scales linearly to user's actual hashrate
- **Greedy knapsack** for power optimization — sorts miners by profit-per-watt descending, packs until budget exhausted

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
| `COINBASE_API_KEY_NAME` | Optional | CDP API key name from Coinbase Developer Platform |
| `COINBASE_API_PRIVATE_KEY` | Optional | CDP API private key (ES256) |
| `DASHBOARD_USERNAME` | Optional | Login username (default: `admin`) |
| `DASHBOARD_PASSWORD` | Optional | Login password. **Leave blank to disable auth** |
| `ELECTRICITY_API_URL` | Optional | URL of companion electricity dashboard (default: `http://127.0.0.1:5001`) |
| `FLASK_HOST` | Optional | Bind address (default: `127.0.0.1`) |
| `FLASK_PORT` | Optional | Port (default: `5000`) |

### Cache TTLs (`config.py`)

| Source | Default TTL | Rationale |
|--------|-------------|-----------|
| WhatToMine | 30 min | Coin prices fluctuate; balance freshness vs rate limits |
| Hashrate.no | 24 hrs | Model-level estimates change slowly |
| MiningNow | 6 hrs | ASIC rankings update infrequently |
| PowerPool | 2 min | Worker status needs near-real-time visibility |
| Coinbase | 5 min | Portfolio balances for dashboard display |

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
  "purchase_date": "2025-06-15",
  "status": "active"
}
```

### Adding Locations

```json
{
  "id": "location-1",
  "name": "garage",
  "electricity_cost_kwh": 0.10,
  "currency": "USD"
}
```

## API Reference

### Profitability

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/profitability` | GET | Full fleet profitability (all sources, suggestions, by-location) |
| `/api/profitability/<id>` | GET | Single miner detailed breakdown |
| `/api/alerts/coin-switch` | GET | Coins more profitable than current primary |
| `/api/history/profit?days=30` | GET | Daily profit snapshots |

### Inventory Management

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/miners` | GET | List all miners |
| `/api/miners` | POST | Add a miner |
| `/api/miners/<id>` | PUT | Update a miner |
| `/api/miners/<id>` | DELETE | Remove a miner |
| `/api/miners/<id>/duplicate` | POST | Clone a miner |
| `/api/locations` | GET/POST | List/add locations |
| `/api/locations/<id>` | PUT/DELETE | Update/remove location |

### Analysis Tools

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/tools/swap-compare` | POST | Compare current vs replacement miner |
| `/api/tools/power-optimize` | POST | Knapsack optimization within watt budget |
| `/api/tools/difficulty` | GET | Historical difficulty trends |
| `/api/pool-summary` | GET | Miner algorithms + pools for comparison page |

### Data Sources

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/sources/whattomine/coins` | GET | Available WhatToMine coins |
| `/api/sources/hashrateno/models` | GET | Hashrate.no miner models |
| `/api/sources/miningnow/models` | GET | MiningNow ASIC database |
| `/api/algorithms` | GET | Supported mining algorithms |
| `/api/cache/status` | GET | Cache age per source |
| `/api/cache/refresh` | POST | Force refresh all caches |
| `/api/cache/refresh/<source>` | POST | Refresh specific source |

### Integrations

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/pool/workers` | GET | PowerPool worker statuses |
| `/api/pool/overview` | GET | Per-algorithm mining overview |
| `/api/pool/revenue` | GET | Pool balance and earnings |
| `/api/wallet/portfolio` | GET | Coinbase portfolio summary |
| `/api/wallet/accounts` | GET | All wallet accounts |
| `/api/electricity/solar-mining` | GET | Real-time solar + mining data |
| `/api/power-import/upload` | POST | Upload CSV power report |

## Profitability Calculation

The engine calculates per-miner profitability as:

```
daily_revenue   = best_of(whattomine, hashrateno, miningnow)
daily_electricity = (wattage / 1000) * 24 * electricity_rate
daily_profit    = daily_revenue - daily_electricity
```

With solar offset applied:

```
solar_offset    = min(solar_daily_kwh / mining_daily_kwh, 1.0)
effective_rate  = base_rate * (1 - solar_offset)
```

ROI calculation:

```
days_to_roi     = total_investment / (daily_profit * quantity)
roi_30d_pct     = (daily_profit * 30 / total_investment) * 100
```

Status thresholds:
- **Profitable** (green): >= $1.00/day
- **Marginal** (yellow): $0.00 - $0.99/day
- **Unprofitable** (red): < $0.00/day

## Project Structure

```
mining-profitability/
├── app.py                      # Flask app, all routes (~1100 lines)
├── config.py                   # Environment config, constants, TTLs
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── services/
│   ├── profitability_engine.py # Core calculation engine
│   ├── whattomine_service.py   # WhatToMine API client
│   ├── hashrateno_service.py   # Hashrate.no API + fuzzy matching
│   ├── miningnow_service.py    # MiningNow scraper (3 strategies)
│   ├── powerpool_service.py    # PowerPool worker monitoring
│   ├── coinbase_service.py     # Coinbase CDP wallet API
│   ├── inventory_manager.py    # JSON-backed miner/location CRUD
│   ├── history_service.py      # SQLite time-series storage
│   ├── cache_manager.py        # TTL file cache
│   └── power_import.py         # CSV power data parser
├── templates/                  # Jinja2 HTML templates
│   ├── base.html               # Nav + layout
│   ├── index.html              # Main dashboard
│   ├── swap.html               # Swap calculator
│   ├── pools.html              # Pool comparison
│   ├── optimizer.html          # Power optimizer
│   ├── difficulty.html         # Difficulty trends
│   ├── wallet.html             # Coinbase wallet
│   └── solar.html              # Solar loan analysis
├── static/
│   ├── css/dashboard.css       # Dark theme, CSS variables
│   └── js/
│       ├── dashboard.js        # Main state + rendering
│       ├── analysis-tools.js   # Swap, optimizer, pool logic
│       ├── charts.js           # Chart.js profit history
│       ├── inventory-modal.js  # Add/edit miner modals
│       ├── solar.js            # Solar loan calculations
│       └── wallet.js           # Wallet display
├── data/                       # User data (gitignored)
│   ├── inventory.json          # Miner definitions
│   ├── locations.json          # Mining locations
│   └── coin_mappings.json      # Algorithm -> coin priorities
└── cache/                      # API response cache (gitignored)
```

## Background Processes

Two daemon threads run alongside the Flask server:

- **Uptime Tracker** — polls PowerPool every 5 minutes, records miner online/offline status and hashrate to SQLite
- **Profit Snapshots** — piggybacks on `/api/profitability` requests, records daily profit per miner (throttled to once per hour)

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Python 3.13, Flask 3.1 |
| Database | SQLite (WAL mode) for history; JSON files for inventory |
| Frontend | Vanilla JS, Chart.js 4.4, CSS3 variables |
| Auth | bcrypt password hashing, Flask sessions |
| Scraping | BeautifulSoup 4, requests |
| Matching | rapidfuzz (fuzzy string matching) |
| Deployment | Docker, Docker Compose |
| Caching | File-based JSON with TTL per source |

## License

MIT
