#!/bin/bash
# Run this on the VM: bash deploy-update.sh
cd ~/mining-profitability

# ---- templates/base.html ----
cat > templates/base.html << 'ENDOFFILE'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Mining Profitability Dashboard{% endblock %}</title>
    <link rel="stylesheet" href="/static/css/dashboard.css?v=7">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
</head>
<body>
    <!-- Nav -->
    <nav class="tool-nav">
        <a href="/" class="tool-nav-link {% if active_page == 'dashboard' %}active{% endif %}">Dashboard</a>
        <a href="/swap" class="tool-nav-link {% if active_page == 'swap' %}active{% endif %}">Swap Calculator</a>
        <a href="/pools" class="tool-nav-link {% if active_page == 'pools' %}active{% endif %}">Pool Comparison</a>
        <a href="/optimizer" class="tool-nav-link {% if active_page == 'optimizer' %}active{% endif %}">Power Optimizer</a>
        <a href="/difficulty" class="tool-nav-link {% if active_page == 'difficulty' %}active{% endif %}">Difficulty Trends</a>
    </nav>

    {% block content %}{% endblock %}

    <!-- Loading overlay -->
    <div class="loading-overlay" id="loadingOverlay" style="display:none;">
        <div class="spinner"></div>
        <p>Loading data...</p>
    </div>

    <script src="/static/js/dashboard.js?v=7"></script>
    {% block scripts %}{% endblock %}
</body>
</html>
ENDOFFILE

# ---- templates/swap.html ----
cat > templates/swap.html << 'ENDOFFILE'
{% extends "base.html" %}
{% block title %}Swap Calculator - Mining Dashboard{% endblock %}

{% block content %}
    <div class="tool-page">
        <div class="tool-page-header">
            <h2>What-If Swap Calculator</h2>
            <p class="tool-desc">Compare your current miner against a potential replacement to see if the upgrade is worth it.</p>
        </div>

        <div class="analysis-panel">
            <div class="swap-form">
                <div class="form-group" style="grid-column:span 2;">
                    <label>Current Miner</label>
                    <select id="swapCurrentMiner"></select>
                </div>
                <div class="form-group">
                    <label>Replacement Model *</label>
                    <input type="text" id="swapRepModel" placeholder="e.g. Antminer S21 XP">
                </div>
                <div class="form-group">
                    <label>Algorithm</label>
                    <select id="swapRepAlgo">
                        <option value="SHA-256">SHA-256</option>
                        <option value="Scrypt">Scrypt</option>
                        <option value="Equihash">Equihash</option>
                        <option value="KHeavyHash">KHeavyHash</option>
                        <option value="Etchash">Etchash</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Hashrate *</label>
                    <input type="number" id="swapRepHashrate" step="any" min="0" placeholder="270">
                </div>
                <div class="form-group">
                    <label>Unit</label>
                    <select id="swapRepUnit">
                        <option value="TH/s">TH/s</option>
                        <option value="GH/s">GH/s</option>
                        <option value="MH/s">MH/s</option>
                        <option value="KH/s">KH/s</option>
                    </select>
                </div>
                <div class="form-group">
                    <label>Wattage *</label>
                    <input type="number" id="swapRepWattage" min="0" placeholder="3583">
                </div>
                <div class="form-group">
                    <label>Purchase Price ($)</label>
                    <input type="number" id="swapRepCost" step="0.01" min="0" placeholder="5500">
                </div>
                <div class="form-group">
                    <label>Current Miner Resale ($)</label>
                    <input type="number" id="swapResaleValue" step="0.01" min="0" placeholder="1000">
                </div>
                <div class="form-group" style="grid-column:span 2;">
                    <button class="btn btn-primary" onclick="runSwapComparison()">Compare</button>
                </div>
            </div>
            <div id="swapResult" style="margin-top:16px;"></div>
        </div>
    </div>
{% endblock %}

{% block scripts %}
    <script src="/static/js/analysis-tools.js?v=7"></script>
    <script>
        var dashboardData = null;
        fetch('/api/profitability')
            .then(r => r.json())
            .then(data => {
                dashboardData = data;
                populateSwapDropdown();
            });
    </script>
{% endblock %}
ENDOFFILE

# ---- templates/pools.html ----
cat > templates/pools.html << 'ENDOFFILE'
{% extends "base.html" %}
{% block title %}Pool Comparison - Mining Dashboard{% endblock %}

{% block content %}
    <div class="tool-page">
        <div class="tool-page-header">
            <h2>Pool Fee Comparison</h2>
            <p class="tool-desc">Compare mining pool fees for your algorithms. Fee loss is based on your current daily revenue.</p>
        </div>

        <div class="analysis-panel">
            <div id="poolComparisonContent">
                <p style="color:var(--text-muted)">Loading pool data...</p>
            </div>
        </div>
    </div>
{% endblock %}

{% block scripts %}
    <script src="/static/js/analysis-tools.js?v=7"></script>
    <script>
        var dashboardData = null;
        fetch('/api/profitability')
            .then(r => r.json())
            .then(data => {
                dashboardData = data;
                renderPoolComparison();
            });
    </script>
{% endblock %}
ENDOFFILE

# ---- templates/optimizer.html ----
cat > templates/optimizer.html << 'ENDOFFILE'
{% extends "base.html" %}
{% block title %}Power Optimizer - Mining Dashboard{% endblock %}

{% block content %}
    <div class="tool-page">
        <div class="tool-page-header">
            <h2>Power Capacity Optimizer</h2>
            <p class="tool-desc">Enter your max wattage budget (e.g. circuit capacity) and see which miners to prioritize for maximum profit.</p>
        </div>

        <div class="analysis-panel">
            <div style="display:flex;gap:10px;align-items:center;margin-bottom:16px;">
                <label>Max Watts:</label>
                <input type="number" id="powerBudgetWatts" min="0" step="100" placeholder="7200" style="width:120px;padding:6px 10px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);color:var(--text);">
                <button class="btn btn-primary" onclick="runPowerOptimizer()">Optimize</button>
            </div>
            <div id="powerOptimizerResult"></div>
        </div>
    </div>
{% endblock %}

{% block scripts %}
    <script src="/static/js/analysis-tools.js?v=7"></script>
{% endblock %}
ENDOFFILE

# ---- templates/difficulty.html ----
cat > templates/difficulty.html << 'ENDOFFILE'
{% extends "base.html" %}
{% block title %}Difficulty Trends - Mining Dashboard{% endblock %}

{% block content %}
    <div class="tool-page">
        <div class="tool-page-header">
            <h2>Difficulty Trends</h2>
            <p class="tool-desc">Track mining difficulty trends. Rising difficulty = shrinking profits.</p>
        </div>

        <div class="analysis-panel">
            <div style="display:flex;gap:8px;margin-bottom:12px;">
                <button class="btn btn-primary" onclick="loadDifficultyData('SHA-256')">SHA-256 (BTC)</button>
                <button class="btn btn-secondary" onclick="loadDifficultyData('Scrypt')">Scrypt (LTC)</button>
                <button class="btn btn-secondary" onclick="loadDifficultyData('Equihash')">Equihash (ZEC)</button>
            </div>
            <div id="difficultyChartContainer">
                <p style="color:var(--text-muted)">Select an algorithm above to load difficulty data.</p>
            </div>
            <div id="difficultyInfo" style="margin-top:8px;font-size:0.85rem;"></div>
        </div>
    </div>
{% endblock %}

{% block scripts %}
    <script src="/static/js/analysis-tools.js?v=7"></script>
{% endblock %}
ENDOFFILE

# ---- templates/index.html (overwrite) ----
cat > templates/index.html << 'ENDOFFILE'
{% extends "base.html" %}
{% block title %}Mining Profitability Dashboard{% endblock %}

{% block content %}
    <!-- Header -->
    <header class="header">
        <div class="header-left">
            <h1>Mining Profitability Dashboard</h1>
            <span class="last-updated" id="lastUpdated">--</span>
        </div>
        <div class="header-right">
            <div class="source-indicators" id="sourceIndicators">
                <span class="source-dot" data-source="whattomine" title="WhatToMine">WTM</span>
                <span class="source-dot" data-source="hashrateno" title="Hashrate.no">HR</span>
                <span class="source-dot" data-source="miningnow" title="MiningNow">MN</span>
            </div>
            <label class="auto-refresh-toggle">
                <input type="checkbox" id="autoRefreshToggle">
                <span>Auto (30m)</span>
            </label>
            <button class="btn btn-primary" id="refreshBtn" onclick="refreshData()">Refresh Data</button>
            <button class="btn btn-secondary" onclick="openPowerImportModal()">CSV Import</button>
            <button class="btn btn-secondary" onclick="openLocationsModal()">Locations</button>
            <button class="btn btn-success" onclick="openMinerModal()">+ Add Miner</button>
        </div>
    </header>

    <!-- Summary Strip -->
    <section class="summary-strip">
        <div class="summary-card">
            <div class="summary-label">Daily Profit</div>
            <div class="summary-value" id="totalDailyProfit">$0.00</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Monthly Profit</div>
            <div class="summary-value" id="totalMonthlyProfit">$0.00</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Total Investment</div>
            <div class="summary-value" id="totalInvestment">$0.00</div>
            <div class="summary-sub" id="portfolioRoi">--</div>
        </div>
        <div class="summary-card">
            <div class="summary-label">Miners</div>
            <div class="summary-value" id="minerCounts">0 / 0</div>
            <div class="summary-sub" id="minerCountsSub">profitable / unprofitable</div>
        </div>
    </section>

    <!-- Location Breakdown -->
    <section class="location-section" id="locationSection">
        <div class="section-header" onclick="toggleLocationSection()">
            <h2>Location Breakdown</h2>
            <span class="toggle-icon" id="locationToggle">&#9660;</span>
        </div>
        <div class="location-grid" id="locationGrid"></div>
    </section>

    <!-- Main Table -->
    <section class="table-section">
        <div class="table-controls">
            <select id="filterType" onchange="applyFilters()">
                <option value="">All Types</option>
                <option value="ASIC">ASIC</option>
                <option value="GPU">GPU</option>
            </select>
            <select id="filterLocation" onchange="applyFilters()">
                <option value="">All Locations</option>
            </select>
            <select id="filterStatus" onchange="applyFilters()">
                <option value="">All Status</option>
                <option value="profitable">Profitable</option>
                <option value="marginal">Marginal</option>
                <option value="unprofitable">Unprofitable</option>
                <option value="inactive">Inactive</option>
            </select>
            <label class="active-only-toggle">
                <input type="checkbox" id="activeOnlyToggle" checked onchange="applyFilters()">
                <span>Active only</span>
            </label>
        </div>
        <div class="table-wrapper">
            <table class="miner-table" id="minerTable">
                <thead>
                    <tr>
                        <th class="sortable" data-col="name" onclick="sortTable('name')">Miner</th>
                        <th class="sortable" data-col="model" onclick="sortTable('model')">Model</th>
                        <th>Type</th>
                        <th>Algo</th>
                        <th class="sortable" data-col="location" onclick="sortTable('location')">Location</th>
                        <th>Hashrate</th>
                        <th>Watts</th>
                        <th>Qty</th>
                        <th class="sortable" data-col="revenue" onclick="sortTable('revenue')" title="Daily mining revenue before electricity costs">Revenue</th>
                        <th class="sortable" data-col="electricity" onclick="sortTable('electricity')" title="Daily electricity cost based on wattage and $/kWh">Elec. Cost</th>
                        <th class="sortable best-profit-col" data-col="best_profit" onclick="sortTable('best_profit')" title="Daily profit after electricity">Daily Profit</th>
                        <th class="sortable" data-col="profit_per_kw" onclick="sortTable('profit_per_kw')" title="Daily profit per kilowatt">$/kW</th>
                        <th class="sortable" data-col="roi_days" onclick="sortTable('roi_days')" title="Days until purchase price is paid back">ROI</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody id="minerTableBody"></tbody>
            </table>
        </div>
        <div class="empty-state" id="emptyState">
            <p>No miners in your inventory yet.</p>
            <button class="btn btn-success" onclick="openMinerModal()">+ Add Your First Miner</button>
        </div>
    </section>

    <!-- Analysis Tools (Breakeven + Scenario stay on dashboard) -->
    <section class="analysis-section">
        <h2 style="padding:0 24px;margin-bottom:12px;">Analysis Tools</h2>

        <!-- Breakeven Electricity Rate -->
        <div class="analysis-panel">
            <div class="section-header" onclick="toggleAnalysisSection('breakeven')">
                <h3>Breakeven Electricity Rate</h3>
                <span class="toggle-icon collapsed" id="breakevenToggle">&#9660;</span>
            </div>
            <div id="breakevenContent" style="display:none;">
                <p class="tool-desc">Shows the $/kWh at which each miner becomes unprofitable. Lower margin = higher risk.</p>
                <div class="table-wrapper">
                    <table class="miner-table" style="font-size:0.85rem;">
                        <thead><tr>
                            <th>Miner</th><th>Current $/kWh</th><th>Breakeven $/kWh</th><th>Margin</th><th>Risk</th>
                        </tr></thead>
                        <tbody id="breakevenTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Price Sensitivity / Scenario -->
        <div class="analysis-panel">
            <div class="section-header" onclick="toggleAnalysisSection('scenario')">
                <h3>Price Scenario Simulator</h3>
                <span class="toggle-icon collapsed" id="scenarioToggle">&#9660;</span>
            </div>
            <div id="scenarioContent" style="display:none;">
                <p class="tool-desc">See how coin price changes affect each miner's profitability.</p>
                <div class="slider-container">
                    <label>Coin Price Change:</label>
                    <input type="range" id="priceSlider" min="10" max="200" value="100" step="5" style="flex:1;">
                    <span id="priceSliderLabel" class="slider-value">+0%</span>
                    <button class="btn btn-sm btn-secondary" onclick="resetPriceSlider()">Reset</button>
                </div>
                <div class="table-wrapper">
                    <table class="miner-table" style="font-size:0.85rem;">
                        <thead><tr>
                            <th>Miner</th><th>Current Profit</th><th>Scenario Profit</th><th>Change</th><th>Status</th>
                        </tr></thead>
                        <tbody id="scenarioTableBody"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </section>

    <!-- Miner Detail Panel -->
    <div class="detail-panel" id="detailPanel" style="display:none;">
        <div class="detail-header">
            <h3 id="detailTitle">Miner Details</h3>
            <button class="btn-close" onclick="closeDetailPanel()">&times;</button>
        </div>
        <div class="detail-content">
            <div class="detail-tabs">
                <button class="tab active" onclick="switchDetailTab('coins')">Coin Breakdown</button>
                <button class="tab" onclick="switchDetailTab('sources')">Source Comparison</button>
                <button class="tab" onclick="switchDetailTab('roi')">ROI Chart</button>
            </div>
            <div class="detail-tab-content" id="detailTabContent"></div>
        </div>
    </div>

    <!-- Add/Edit Miner Modal -->
    <div class="modal-overlay" id="minerModal" style="display:none;">
        <div class="modal">
            <div class="modal-header">
                <h3 id="minerModalTitle">Add Miner</h3>
                <button class="btn-close" onclick="closeMinerModal()">&times;</button>
            </div>
            <form id="minerForm" onsubmit="saveMiner(event)">
                <input type="hidden" id="minerFormId">
                <div class="form-grid">
                    <div class="form-group">
                        <label>Name *</label>
                        <input type="text" id="minerName" required placeholder="My Antminer S21 #1">
                    </div>
                    <div class="form-group">
                        <label>Model *</label>
                        <input type="text" id="minerModel" required placeholder="Antminer S21">
                    </div>
                    <div class="form-group">
                        <label>Type *</label>
                        <select id="minerType" required>
                            <option value="ASIC">ASIC</option>
                            <option value="GPU">GPU</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Algorithm *</label>
                        <select id="minerAlgorithm" required>
                            <option value="">Select...</option>
                        </select>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label>Hashrate *</label>
                            <input type="number" id="minerHashrate" required step="any" min="0" placeholder="200">
                        </div>
                        <div class="form-group">
                            <label>Unit *</label>
                            <select id="minerHashrateUnit" required>
                                <option value="TH/s">TH/s</option>
                                <option value="GH/s">GH/s</option>
                                <option value="MH/s">MH/s</option>
                                <option value="KH/s">KH/s</option>
                                <option value="H/s">H/s</option>
                                <option value="Sol/s">Sol/s</option>
                                <option value="KSol/s">KSol/s</option>
                            </select>
                        </div>
                    </div>
                    <div class="form-group">
                        <label>Wattage *</label>
                        <input type="number" id="minerWattage" required min="0" placeholder="3500">
                    </div>
                    <div class="form-group">
                        <label>Location *</label>
                        <select id="minerLocation" required></select>
                    </div>
                    <div class="form-group">
                        <label>Quantity</label>
                        <input type="number" id="minerQuantity" value="1" min="1">
                    </div>
                    <div class="form-group">
                        <label>Purchase Price ($)</label>
                        <input type="number" id="minerPurchasePrice" step="0.01" min="0" placeholder="5500">
                    </div>
                    <div class="form-group">
                        <label>Purchase Date</label>
                        <input type="date" id="minerPurchaseDate">
                    </div>
                    <div class="form-group">
                        <label>Status</label>
                        <select id="minerStatus">
                            <option value="active">Active</option>
                            <option value="inactive">Inactive</option>
                        </select>
                    </div>
                </div>
                <details class="advanced-mappings">
                    <summary>Advanced: Source Mapping Overrides</summary>
                    <div class="form-grid">
                        <div class="form-group">
                            <label>Hashrate.no Model Key</label>
                            <input type="text" id="minerHrnKey" placeholder="Auto-matched by model name">
                        </div>
                        <div class="form-group">
                            <label>MiningNow Model Key</label>
                            <input type="text" id="minerMnKey" placeholder="Auto-matched by model name">
                        </div>
                    </div>
                </details>
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeMinerModal()">Cancel</button>
                    <button type="submit" class="btn btn-success">Save Miner</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Locations Modal -->
    <div class="modal-overlay" id="locationsModal" style="display:none;">
        <div class="modal">
            <div class="modal-header">
                <h3>Manage Locations</h3>
                <button class="btn-close" onclick="closeLocationsModal()">&times;</button>
            </div>
            <div class="locations-list" id="locationsList"></div>
            <div class="form-actions">
                <button class="btn btn-success" onclick="addLocationRow()">+ Add Location</button>
                <button class="btn btn-secondary" onclick="closeLocationsModal()">Close</button>
            </div>
        </div>
    </div>

    <!-- CSV Power Import Modal -->
    <div class="modal-overlay" id="powerImportModal" style="display:none;">
        <div class="modal">
            <div class="modal-header">
                <h3>CSV Power Report Import</h3>
                <button class="btn-close" onclick="closePowerImportModal()">&times;</button>
            </div>
            <div class="power-import-content">
                <p style="color:var(--text-muted);font-size:0.85rem;margin-bottom:16px;">
                    Upload a power report CSV to get actual wattage readings for your miners.
                    This data will override nameplate wattage in profitability calculations.
                </p>
                <div class="power-import-upload-area" id="powerImportUploadArea">
                    <input type="file" id="powerImportFileInput" accept=".csv" style="display:none" onchange="uploadPowerCSV(this)">
                    <button class="btn btn-primary" onclick="document.getElementById('powerImportFileInput').click()">
                        Upload CSV File
                    </button>
                    <span id="powerImportFileName" style="margin-left:10px;color:var(--text-muted);font-size:0.85rem;"></span>
                </div>
                <div id="powerImportStatus" style="margin-top:16px;"></div>
                <div id="powerImportDataSummary" style="margin-top:16px;"></div>
                <div id="powerImportAddSection" style="display:none;margin-top:16px;border-top:1px solid var(--border);padding-top:12px;">
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
                        <label style="color:var(--text-muted);font-size:0.85rem;">Location:</label>
                        <select id="powerImportLocation" style="flex:1;"></select>
                        <button class="btn btn-success btn-sm" onclick="addSelectedMiners()">Add Selected to Inventory</button>
                    </div>
                </div>
            </div>
            <div class="form-actions">
                <button class="btn btn-danger btn-sm" onclick="clearPowerData()" id="powerImportClearBtn" style="display:none;">Clear Imported Data</button>
                <button class="btn btn-secondary" onclick="closePowerImportModal()">Close</button>
            </div>
        </div>
    </div>
{% endblock %}

{% block scripts %}
    <script src="/static/js/inventory-modal.js?v=7"></script>
    <script src="/static/js/charts.js?v=7"></script>
    <script src="/static/js/analysis-tools.js?v=7"></script>
{% endblock %}
ENDOFFILE

echo "Templates updated successfully!"
echo "Now updating app.py, dashboard.js, and dashboard.css..."

# ---- Update app.py: add new routes after index route ----
# We need to check if routes already exist
if ! grep -q "def swap_page" app.py; then
    sed -i '/^@app.route("\/")$/,/return render_template("index.html")/{
        s/return render_template("index.html")/return render_template("index.html", active_page="dashboard")/
    }' app.py

    sed -i '/return render_template("index.html", active_page="dashboard")/a\
\
\
@app.route("/swap")\
def swap_page():\
    return render_template("swap.html", active_page="swap")\
\
\
@app.route("/pools")\
def pools_page():\
    return render_template("pools.html", active_page="pools")\
\
\
@app.route("/optimizer")\
def optimizer_page():\
    return render_template("optimizer.html", active_page="optimizer")\
\
\
@app.route("/difficulty")\
def difficulty_page():\
    return render_template("difficulty.html", active_page="difficulty")' app.py
    echo "app.py routes added"
else
    echo "app.py routes already exist, skipping"
fi

# ---- Update dashboard.js: guard init for non-dashboard pages ----
if ! grep -q "var autoRefreshEl" static/js/dashboard.js; then
    sed -i "s/document.addEventListener('DOMContentLoaded', () => {/document.addEventListener('DOMContentLoaded', () => {\n    \/\/ Only run full dashboard init on the main page\n    var autoRefreshEl = document.getElementById('autoRefreshToggle');\n    if (!autoRefreshEl) return;/" static/js/dashboard.js
    # Remove the old getElementById line that would now be duplicate
    sed -i "/document.getElementById('autoRefreshToggle').addEventListener/s/document.getElementById('autoRefreshToggle')/autoRefreshEl/" static/js/dashboard.js
    echo "dashboard.js updated"
else
    echo "dashboard.js already updated, skipping"
fi

# ---- Update dashboard.css: add nav styles at top ----
if ! grep -q "tool-nav" static/css/dashboard.css; then
    sed -i '/^\* { margin: 0; padding: 0; box-sizing: border-box; }$/a\
\
/* Tool Navigation */\
.tool-nav {\
    display: flex;\
    gap: 0;\
    background: var(--surface);\
    border-bottom: 1px solid var(--border);\
    padding: 0 24px;\
    overflow-x: auto;\
}\
.tool-nav-link {\
    padding: 10px 18px;\
    color: var(--text-muted);\
    text-decoration: none;\
    font-size: 0.85rem;\
    font-weight: 500;\
    border-bottom: 2px solid transparent;\
    transition: color 0.2s, border-color 0.2s;\
    white-space: nowrap;\
}\
.tool-nav-link:hover { color: var(--text); }\
.tool-nav-link.active {\
    color: var(--primary);\
    border-bottom-color: var(--primary);\
}\
\
/* Tool page layout */\
.tool-page { padding: 24px; }\
.tool-page-header { margin-bottom: 20px; }\
.tool-page-header h2 { font-size: 1.3rem; font-weight: 600; margin-bottom: 6px; }\
.tool-page .analysis-panel { padding: 20px; }' static/css/dashboard.css
    echo "dashboard.css updated"
else
    echo "dashboard.css already has nav styles, skipping"
fi

echo ""
echo "All files updated! Now restart:"
echo "  docker compose down && docker compose build --no-cache && docker compose up -d"
