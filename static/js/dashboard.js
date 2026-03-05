// ---- State ----
let dashboardData = null;
let miners = [];
let locations = [];
let sortColumn = 'best_profit';
let sortDirection = 'desc';
let expandedMinerId = null;
let autoRefreshInterval = null;

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    // Only run full dashboard init on the main page
    var autoRefreshEl = document.getElementById('autoRefreshToggle');
    if (!autoRefreshEl) return;

    loadDashboard();
    loadLocations();
    loadAlgorithms();

    autoRefreshEl.addEventListener('change', (e) => {
        if (e.target.checked) {
            autoRefreshInterval = setInterval(loadDashboard, 30 * 60 * 1000);
        } else {
            clearInterval(autoRefreshInterval);
            autoRefreshInterval = null;
        }
    });
});

// ---- Data Loading ----
async function loadDashboard() {
    showLoading(true);
    try {
        const resp = await fetch('/api/profitability');
        if (!resp.ok) throw new Error('Failed to load profitability data');
        dashboardData = await resp.json();
        renderSummary(dashboardData.summary);
        renderLocationBreakdown(dashboardData.by_location);
        renderMinerTable(dashboardData.miners);
        updateSourceIndicators(dashboardData.cache_status);
        document.getElementById('lastUpdated').textContent =
            'Updated: ' + new Date(dashboardData.last_updated).toLocaleString();
    } catch (err) {
        showToast('Error loading data: ' + err.message, 'error');
    }
    showLoading(false);
}

async function loadLocations() {
    try {
        const resp = await fetch('/api/locations');
        locations = await resp.json();
        populateLocationDropdowns();
    } catch (err) {
        console.error('Failed to load locations', err);
    }
}

async function loadAlgorithms() {
    try {
        const resp = await fetch('/api/algorithms');
        const algos = await resp.json();
        const select = document.getElementById('minerAlgorithm');
        // Keep the first "Select..." option
        while (select.options.length > 1) select.remove(1);
        algos.forEach(a => {
            const opt = document.createElement('option');
            opt.value = a;
            opt.textContent = a;
            select.appendChild(opt);
        });
    } catch (err) {
        console.error('Failed to load algorithms', err);
    }
}

async function refreshData() {
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    try {
        await fetch('/api/cache/refresh', { method: 'POST' });
        await loadDashboard();
        showToast('Data refreshed', 'success');
    } catch (err) {
        showToast('Refresh failed: ' + err.message, 'error');
    }
    btn.disabled = false;
    btn.textContent = 'Refresh Data';
}

// ---- Rendering ----
function renderSummary(summary) {
    if (!summary) return;
    const dp = summary.total_daily_profit;
    const mp = summary.total_monthly_profit;

    setProfit('totalDailyProfit', dp);
    setProfit('totalMonthlyProfit', mp);
    document.getElementById('totalInvestment').textContent = formatCurrency(summary.total_investment);
    document.getElementById('portfolioRoi').textContent =
        summary.portfolio_roi_days > 0 ? `ROI in ${summary.portfolio_roi_days} days` : '--';
    document.getElementById('minerCounts').textContent =
        `${summary.profitable_count} / ${summary.unprofitable_count}`;
    document.getElementById('minerCountsSub').textContent =
        `profitable / unprofitable (${summary.marginal_count} marginal)`;
}

function setProfit(elementId, value) {
    const el = document.getElementById(elementId);
    el.textContent = formatCurrency(value);
    el.className = 'summary-value ' + (value > 0 ? 'profit-positive' : value < 0 ? 'profit-negative' : 'profit-neutral');
}

function renderLocationBreakdown(byLocation) {
    const grid = document.getElementById('locationGrid');
    if (!byLocation || Object.keys(byLocation).length === 0) {
        document.getElementById('locationSection').style.display = 'none';
        return;
    }
    document.getElementById('locationSection').style.display = 'block';
    grid.innerHTML = '';
    for (const [name, data] of Object.entries(byLocation)) {
        const card = document.createElement('div');
        card.className = 'location-card';
        card.innerHTML = `
            <div class="location-info">
                <span class="location-name">${esc(name)}</span>
                <span class="location-detail">${data.units} units &middot; $${data.electricity_cost_kwh}/kWh</span>
            </div>
            <span class="location-profit ${data.daily_profit >= 0 ? 'profit-positive' : 'profit-negative'}">
                ${formatCurrency(data.daily_profit)}/day
            </span>
        `;
        grid.appendChild(card);
    }
}

function renderMinerTable(minerResults) {
    const tbody = document.getElementById('minerTableBody');
    const empty = document.getElementById('emptyState');
    const tableWrapper = document.querySelector('.table-wrapper');

    if (!minerResults || minerResults.length === 0) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        tableWrapper.style.display = 'none';
        return;
    }

    empty.style.display = 'none';
    tableWrapper.style.display = 'block';

    // Apply filters
    let filtered = applyFilterLogic(minerResults);

    // Sort
    filtered = sortData(filtered);

    tbody.innerHTML = '';
    filtered.forEach(r => {
        const m = r.miner;
        const loc = r.location;
        const wtm = r.sources.whattomine;
        const hrn = r.sources.hashrateno;
        const mn = r.sources.miningnow;
        const best = r.best_daily_profit || 0;
        const profitPerKw = getProfitPerKw(r);

        const tr = document.createElement('tr');
        tr.className = r.status === expandedMinerId ? 'expanded' : '';
        tr.onclick = () => openDetailPanel(r);
        tr.innerHTML = `
            <td>${esc(m.name)}</td>
            <td>${esc(m.model)}</td>
            <td>${m.type}</td>
            <td>${esc(m.algorithm)}</td>
            <td>${esc(loc.name || '--')}</td>
            <td>${m.hashrate} ${m.hashrate_unit}</td>
            <td>${formatWatts(r.power)}</td>
            <td>${m.quantity || 1}</td>
            <td>${formatCurrency(r.daily_revenue)}</td>
            <td style="color:var(--profit-red)">${formatCurrency(r.daily_electricity)}</td>
            <td class="best-profit ${profitClass(best)}"><strong>${formatCurrency(best)}</strong></td>
            <td class="${profitClass(profitPerKw)}">${formatCurrency(profitPerKw)}</td>
            <td>${r.roi && r.roi.days_to_roi > 0 ? r.roi.days_to_roi + 'd' : '--'}</td>
            <td><span class="status-badge status-${r.status}">${r.status}</span></td>
            <td class="action-btns" onclick="event.stopPropagation()">
                <button class="btn-sm" onclick="editMiner('${m.id}')" title="Edit">&#9998;</button>
                <button class="btn-sm" onclick="duplicateMiner('${m.id}')" title="Duplicate">&#10697;</button>
                <button class="btn-sm delete" onclick="deleteMiner('${m.id}')" title="Delete">&#10005;</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    // Totals row
    let totalWatts = 0, totalQty = 0, totalRevenue = 0, totalElec = 0, totalProfit = 0;
    filtered.forEach(r => {
        const qty = r.miner.quantity || 1;
        totalWatts += (r.power ? r.power.effective_watts || r.power.nameplate_watts : r.miner.wattage || 0) * qty;
        totalQty += qty;
        totalRevenue += (r.daily_revenue || 0) * qty;
        totalElec += (r.daily_electricity || 0) * qty;
        totalProfit += (r.best_daily_profit || 0) * qty;
    });
    const totalTr = document.createElement('tr');
    totalTr.className = 'totals-row';
    totalTr.innerHTML = `
        <td colspan="6" style="text-align:right;font-weight:700;">Totals</td>
        <td style="font-weight:700;">${Math.round(totalWatts)}W</td>
        <td style="font-weight:700;">${totalQty}</td>
        <td style="font-weight:700;">${formatCurrency(totalRevenue)}</td>
        <td style="font-weight:700;color:var(--profit-red)">${formatCurrency(totalElec)}</td>
        <td class="best-profit ${profitClass(totalProfit)}" style="font-weight:700;">${formatCurrency(totalProfit)}</td>
        <td style="font-weight:700;" class="${profitClass(totalWatts > 0 ? (totalProfit / totalWatts) * 1000 : 0)}">${totalWatts > 0 ? formatCurrency((totalProfit / totalWatts) * 1000) : '--'}</td>
        <td colspan="3"></td>
    `;
    tbody.appendChild(totalTr);

    // Update sort headers
    document.querySelectorAll('.miner-table th').forEach(th => {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.col === sortColumn) {
            th.classList.add(sortDirection === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });
}

function sortTable(column) {
    if (sortColumn === column) {
        sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
    } else {
        sortColumn = column;
        sortDirection = 'desc';
    }
    if (dashboardData) renderMinerTable(dashboardData.miners);
}

function sortData(data) {
    return [...data].sort((a, b) => {
        let va, vb;
        switch (sortColumn) {
            case 'name': va = a.miner.name; vb = b.miner.name; break;
            case 'model': va = a.miner.model; vb = b.miner.model; break;
            case 'location': va = a.location.name || ''; vb = b.location.name || ''; break;
            case 'revenue': va = a.daily_revenue || 0; vb = b.daily_revenue || 0; break;
            case 'electricity': va = a.daily_electricity || 0; vb = b.daily_electricity || 0; break;
            case 'best_profit': va = a.best_daily_profit || 0; vb = b.best_daily_profit || 0; break;
            case 'profit_per_kw': va = getProfitPerKw(a); vb = getProfitPerKw(b); break;
            case 'roi_days':
                va = a.roi && a.roi.days_to_roi > 0 ? a.roi.days_to_roi : 99999;
                vb = b.roi && b.roi.days_to_roi > 0 ? b.roi.days_to_roi : 99999;
                break;
            default: va = 0; vb = 0;
        }
        if (typeof va === 'string') {
            return sortDirection === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return sortDirection === 'asc' ? va - vb : vb - va;
    });
}

function applyFilters() {
    if (dashboardData) renderMinerTable(dashboardData.miners);
}

function applyFilterLogic(data) {
    const typeFilter = document.getElementById('filterType').value;
    const locFilter = document.getElementById('filterLocation').value;
    const statusFilter = document.getElementById('filterStatus').value;
    const activeOnly = document.getElementById('activeOnlyToggle').checked;

    return data.filter(r => {
        if (activeOnly && r.status === 'inactive') return false;
        if (typeFilter && r.miner.type !== typeFilter) return false;
        if (locFilter && r.miner.location_id !== locFilter) return false;
        if (statusFilter && r.status !== statusFilter) return false;
        return true;
    });
}

function populateLocationDropdowns() {
    const filterSelect = document.getElementById('filterLocation');
    const minerSelect = document.getElementById('minerLocation');

    // Filter dropdown
    while (filterSelect.options.length > 1) filterSelect.remove(1);
    locations.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.id;
        opt.textContent = `${l.name} ($${l.electricity_cost_kwh}/kWh)`;
        filterSelect.appendChild(opt);
    });

    // Miner form dropdown
    minerSelect.innerHTML = '';
    locations.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.id;
        opt.textContent = `${l.name} ($${l.electricity_cost_kwh}/kWh)`;
        minerSelect.appendChild(opt);
    });
}

function updateSourceIndicators(cacheStatus) {
    if (!cacheStatus) return;
    const dots = document.querySelectorAll('.source-dot');
    dots.forEach(dot => {
        const source = dot.dataset.source;
        const info = cacheStatus[source + '_age'];
        if (!info || info === 'No data') {
            dot.className = 'source-dot unavailable';
            dot.title += ' - No data';
        } else if (info.includes('h') && parseInt(info) > 12) {
            dot.className = 'source-dot stale';
            dot.title += ' - ' + info;
        } else {
            dot.className = 'source-dot fresh';
            dot.title += ' - ' + info;
        }
    });
}

// ---- Detail Panel ----
function openDetailPanel(minerResult) {
    expandedMinerId = minerResult.miner.id;
    const panel = document.getElementById('detailPanel');
    document.getElementById('detailTitle').textContent = minerResult.miner.name;
    panel.style.display = 'block';
    switchDetailTab('coins', minerResult);
}

function closeDetailPanel() {
    document.getElementById('detailPanel').style.display = 'none';
    expandedMinerId = null;
}

function switchDetailTab(tab, minerResult) {
    const tabs = document.querySelectorAll('.detail-tabs .tab');
    tabs.forEach(t => t.classList.remove('active'));
    event.target ? event.target.classList.add('active') : tabs[0].classList.add('active');

    const content = document.getElementById('detailTabContent');
    if (!minerResult && dashboardData) {
        minerResult = dashboardData.miners.find(r => r.miner.id === expandedMinerId);
    }
    if (!minerResult) { content.innerHTML = '<p>No data available</p>'; return; }

    if (tab === 'coins') {
        renderCoinBreakdown(content, minerResult);
    } else if (tab === 'sources') {
        renderSourceComparison(content, minerResult);
    } else if (tab === 'roi') {
        renderROITab(content, minerResult);
    }
}

function renderCoinBreakdown(container, result) {
    const coins = result.sources.whattomine.all_coins || [];
    if (coins.length === 0) {
        container.innerHTML = '<p style="color:var(--text-muted)">No WhatToMine data available for this miner.</p>';
        return;
    }
    let html = `<table class="coin-table"><thead><tr>
        <th>Coin</th><th>Revenue</th><th>Electricity</th><th>Profit</th><th>Rewards</th>
    </tr></thead><tbody>`;
    coins.forEach(c => {
        html += `<tr>
            <td><strong>${esc(c.tag)}</strong> <span style="color:var(--text-muted)">${esc(c.coin_name)}</span></td>
            <td>${formatCurrency(c.daily_revenue)}</td>
            <td style="color:var(--profit-red)">${formatCurrency(c.daily_electricity)}</td>
            <td class="${profitClass(c.daily_profit)}"><strong>${formatCurrency(c.daily_profit)}</strong></td>
            <td style="color:var(--text-muted)">${c.estimated_rewards}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    container.innerHTML = html;
}

function renderSourceComparison(container, result) {
    const wtm = result.sources.whattomine;
    const hrn = result.sources.hashrateno;
    const mn = result.sources.miningnow;

    container.innerHTML = `
        <div class="source-grid">
            <div class="source-card">
                <h4><span class="source-dot ${wtm.available ? 'fresh' : 'unavailable'}" style="display:inline-block;width:8px;height:8px;border-radius:50%"></span> WhatToMine</h4>
                <p>Best Coin: <span class="value">${wtm.best_coin || '--'}</span></p>
                <p>Revenue: <span class="value">${wtm.available ? formatCurrency(wtm.daily_revenue) : '--'}</span></p>
                <p>Electricity: <span class="value">${wtm.available ? formatCurrency(wtm.daily_electricity) : '--'}</span></p>
                <p>Profit: <span class="value ${profitClass(wtm.daily_profit)}">${wtm.available ? formatCurrency(wtm.daily_profit) : '--'}</span></p>
            </div>
            <div class="source-card">
                <h4><span class="source-dot ${hrn.available ? 'fresh' : 'unavailable'}" style="display:inline-block;width:8px;height:8px;border-radius:50%"></span> Hashrate.no</h4>
                <p>Matched: <span class="value">${hrn.matched_model || '--'}</span></p>
                <p>Confidence: <span class="value">${hrn.match_confidence ? hrn.match_confidence + '%' : '--'}</span></p>
                <p>Revenue: <span class="value">${hrn.available ? formatCurrency(hrn.daily_revenue) : '--'}</span></p>
                <p>Profit: <span class="value ${profitClass(hrn.daily_profit)}">${hrn.available ? formatCurrency(hrn.daily_profit) : '--'}</span></p>
            </div>
            <div class="source-card">
                <h4><span class="source-dot ${mn.available ? 'fresh' : 'unavailable'}" style="display:inline-block;width:8px;height:8px;border-radius:50%"></span> MiningNow</h4>
                <p>Matched: <span class="value">${mn.matched_model || '--'}</span></p>
                <p>Rank: <span class="value">${mn.rank || '--'}</span></p>
                <p>Score: <span class="value">${mn.profitability_score || '--'}</span></p>
                <p>Best Price: <span class="value">${mn.best_price ? formatCurrency(mn.best_price) : '--'}</span></p>
            </div>
        </div>
    `;
}

function renderROITab(container, result) {
    const roi = result.roi;
    if (!roi) { container.innerHTML = '<p>No ROI data.</p>'; return; }

    container.innerHTML = `
        <div style="margin-bottom:16px">
            <p>Investment: <strong>${formatCurrency(roi.total_investment)}</strong></p>
            <p>Daily Profit: <strong class="${profitClass(roi.best_daily_profit)}">${formatCurrency(roi.best_daily_profit)}</strong></p>
            <p>Monthly Profit: <strong class="${profitClass(roi.best_monthly_profit)}">${formatCurrency(roi.best_monthly_profit)}</strong></p>
            <p>Days to ROI: <strong>${roi.days_to_roi > 0 ? roi.days_to_roi : 'N/A'}</strong></p>
            <p>Payback Date: <strong>${roi.estimated_payback_date || 'N/A'}</strong></p>
            <p>30-Day ROI: <strong>${roi.roi_percentage_30d ? roi.roi_percentage_30d.toFixed(1) + '%' : 'N/A'}</strong></p>
        </div>
        <canvas id="roiChart"></canvas>
    `;

    if (roi.days_to_roi > 0 && typeof renderROIChart === 'function') {
        renderROIChart(roi);
    }
}

// ---- Location Section Toggle ----
function toggleLocationSection() {
    const grid = document.getElementById('locationGrid');
    const icon = document.getElementById('locationToggle');
    if (grid.style.display === 'none') {
        grid.style.display = 'grid';
        icon.classList.remove('collapsed');
    } else {
        grid.style.display = 'none';
        icon.classList.add('collapsed');
    }
}

// ---- CRUD Actions ----
async function deleteMiner(id) {
    if (!confirm('Delete this miner?')) return;
    try {
        await fetch(`/api/miners/${id}`, { method: 'DELETE' });
        showToast('Miner deleted', 'success');
        loadDashboard();
    } catch (err) {
        showToast('Delete failed', 'error');
    }
}

async function duplicateMiner(id) {
    try {
        await fetch(`/api/miners/${id}/duplicate`, { method: 'POST' });
        showToast('Miner duplicated', 'success');
        loadDashboard();
    } catch (err) {
        showToast('Duplicate failed', 'error');
    }
}

// ---- Helpers ----
function getProfitPerKw(r) {
    var w = r.power ? (r.power.effective_watts || r.power.nameplate_watts) : (r.miner.wattage || 0);
    return w > 0 ? ((r.best_daily_profit || 0) / w) * 1000 : 0;
}

function formatCurrency(amount) {
    if (amount == null || isNaN(amount)) return '--';
    const prefix = amount < 0 ? '-$' : '$';
    return prefix + Math.abs(amount).toFixed(2);
}

function profitClass(val) {
    if (val == null) return '';
    if (val >= 1) return 'profit-positive';
    if (val >= 0) return 'profit-neutral';
    return 'profit-negative';
}

function esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function showLoading(show) {
    document.getElementById('loadingOverlay').style.display = show ? 'flex' : 'none';
}

function formatWatts(power) {
    if (!power) return '--';
    if (power.source === 'csv_import') {
        return `<span title="Imported actual: ${Math.round(power.actual_watts)}W | Nameplate: ${power.nameplate_watts}W" style="border-bottom:1px dotted var(--success)">${Math.round(power.actual_watts)}W</span>`;
    }
    return `${power.nameplate_watts}W`;
}

function showToast(message, type = '') {
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}

// ---- CSV Power Import ----
function openPowerImportModal() {
    document.getElementById('powerImportModal').style.display = 'flex';
    loadPowerImportStatus();
    // Populate location dropdown
    const locSelect = document.getElementById('powerImportLocation');
    locSelect.innerHTML = '';
    locations.forEach(l => {
        const opt = document.createElement('option');
        opt.value = l.id;
        opt.textContent = `${l.name} ($${l.electricity_cost_kwh}/kWh)`;
        locSelect.appendChild(opt);
    });
}

function closePowerImportModal() {
    document.getElementById('powerImportModal').style.display = 'none';
}

// Model-to-algorithm mapping for auto-detection
const MODEL_ALGO_MAP = {
    // SHA-256
    's21': 'SHA-256', 's19': 'SHA-256', 's17': 'SHA-256', 's15': 'SHA-256', 's9': 'SHA-256',
    't21': 'SHA-256', 't19': 'SHA-256', 't17': 'SHA-256',
    'a2 pro': 'SHA-256', 'sealminer a2': 'SHA-256',
    'whatsminer m60': 'SHA-256', 'whatsminer m50': 'SHA-256', 'whatsminer m30': 'SHA-256',
    'avalon': 'SHA-256',
    // Scrypt
    'l9': 'Scrypt', 'l7': 'Scrypt', 'l3': 'Scrypt',
    'dg1': 'Scrypt', 'elphapex': 'Scrypt',
    // KHeavyHash (Kaspa)
    'ks3': 'KHeavyHash', 'ks5': 'KHeavyHash', 'ks0': 'KHeavyHash',
    'iceriver': 'KHeavyHash',
    // Equihash
    'z15': 'Equihash', 'z11': 'Equihash',
    // X11
    'd9': 'X11', 'd7': 'X11',
    // Eaglesong
    'ck5': 'Eaglesong',
    // Blake3
    'al1': 'Blake3',
};

function detectAlgorithm(modelStr) {
    const lower = modelStr.toLowerCase();
    for (const [pattern, algo] of Object.entries(MODEL_ALGO_MAP)) {
        if (lower.includes(pattern)) return algo;
    }
    return '';
}

function formatHashrate(hashesPerSecond) {
    if (!hashesPerSecond || hashesPerSecond <= 0) return { value: 0, unit: 'TH/s' };
    const units = [
        { threshold: 1e15, unit: 'PH/s' },
        { threshold: 1e12, unit: 'TH/s' },
        { threshold: 1e9, unit: 'GH/s' },
        { threshold: 1e6, unit: 'MH/s' },
        { threshold: 1e3, unit: 'KH/s' },
    ];
    for (const u of units) {
        if (hashesPerSecond >= u.threshold) {
            return { value: parseFloat((hashesPerSecond / u.threshold).toFixed(2)), unit: u.unit };
        }
    }
    return { value: Math.round(hashesPerSecond), unit: 'H/s' };
}

function extractModelName(minerType) {
    // "Antminer L9 (17G)" → "Antminer L9"
    // "SealMiner A2 Pro" → "SealMiner A2 Pro"
    // "ElphaPex DG1" → "ElphaPex DG1"
    return minerType.replace(/\s*\([^)]*\)\s*$/, '').trim();
}

async function loadPowerImportStatus() {
    try {
        const resp = await fetch('/api/power-import/data');
        const data = await resp.json();
        const summary = document.getElementById('powerImportDataSummary');
        const clearBtn = document.getElementById('powerImportClearBtn');
        const addSection = document.getElementById('powerImportAddSection');
        const miners = data.miners || {};
        const count = Object.keys(miners).length;

        // Get existing inventory miner names for comparison
        const inventoryNames = (dashboardData?.miners || []).map(r => r.miner.name.toLowerCase());

        if (count > 0) {
            clearBtn.style.display = 'inline-block';
            let hasUnmatched = false;
            let html = `<p style="color:var(--success);font-size:0.85rem;margin-bottom:8px;">
                ${count} miners imported (last: ${data.last_import ? new Date(data.last_import).toLocaleString() : 'unknown'})
            </p><table class="coin-table"><thead><tr>
                <th style="width:30px;"></th>
                <th>Miner Name</th><th>Model</th><th>Avg Watts</th><th>Uptime</th><th>Days</th><th>In Inventory</th>
            </tr></thead><tbody>`;
            for (const [name, info] of Object.entries(miners)) {
                const model = extractModelName(info.miner_type || '');
                // Check if already in inventory (substring match like the backend does)
                const nameLower = name.toLowerCase();
                const inInventory = inventoryNames.some(inv =>
                    inv.includes(nameLower) || nameLower.includes(inv)
                ) || (dashboardData?.miners || []).some(r => {
                    const n = r.miner.name.toLowerCase();
                    return n.includes(nameLower) || nameLower.includes(n);
                });

                if (!inInventory) hasUnmatched = true;

                html += `<tr style="${inInventory ? 'opacity:0.5;' : ''}">
                    <td><input type="checkbox" class="csv-miner-check" data-name="${esc(name)}" ${inInventory ? 'disabled title="Already in inventory"' : ''}></td>
                    <td>${esc(name)}</td>
                    <td style="color:var(--text-muted);font-size:0.85rem;">${esc(model)}</td>
                    <td>${Math.round(info.avg_power_watts)}W</td>
                    <td>${info.avg_uptime_pct.toFixed(1)}%</td>
                    <td>${info.days_in_report}</td>
                    <td>${inInventory ? '<span style="color:var(--success)">Yes</span>' : '<span style="color:var(--text-muted)">No</span>'}</td>
                </tr>`;
            }
            html += '</tbody></table>';
            summary.innerHTML = html;

            // Show add section if there are miners not yet in inventory
            addSection.style.display = hasUnmatched ? 'block' : 'none';
        } else {
            clearBtn.style.display = 'none';
            addSection.style.display = 'none';
            summary.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;">No power data imported yet.</p>';
        }
    } catch (err) {
        console.error('Failed to load power import data', err);
    }
}

async function addSelectedMiners() {
    const checkboxes = document.querySelectorAll('.csv-miner-check:checked');
    if (checkboxes.length === 0) {
        showToast('Select at least one miner to add', 'error');
        return;
    }

    const locationId = document.getElementById('powerImportLocation').value;
    if (!locationId) {
        showToast('Please select a location', 'error');
        return;
    }

    // Get full power import data for metadata
    const resp = await fetch('/api/power-import/data');
    const powerData = await resp.json();
    const csvMiners = powerData.miners || {};

    let added = 0;
    for (const cb of checkboxes) {
        const csvName = cb.dataset.name;
        const info = csvMiners[csvName];
        if (!info) continue;

        const model = extractModelName(info.miner_type || '');
        const algo = detectAlgorithm(info.miner_type || csvName);
        const hr = formatHashrate(info.theoretical_hash_rate || 0);
        const watts = Math.round(info.avg_power_watts) || 0;

        const minerData = {
            name: csvName,
            model: model || csvName,
            type: 'ASIC',
            algorithm: algo,
            hashrate: hr.value,
            hashrate_unit: hr.unit,
            wattage: watts,
            location_id: locationId,
            quantity: 1,
            purchase_price: 0,
            purchase_date: '',
            status: 'active',
        };

        try {
            const addResp = await fetch('/api/miners', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(minerData),
            });
            if (addResp.ok) added++;
        } catch (err) {
            console.error('Failed to add miner', csvName, err);
        }
    }

    showToast(`Added ${added} miner(s) to inventory`, 'success');
    await loadDashboard();
    loadPowerImportStatus();
}

async function uploadPowerCSV(input) {
    const file = input.files[0];
    if (!file) return;

    document.getElementById('powerImportFileName').textContent = file.name;
    const status = document.getElementById('powerImportStatus');
    status.innerHTML = '<p style="color:var(--text-muted)">Uploading...</p>';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const resp = await fetch('/api/power-import/upload', { method: 'POST', body: formData });
        const result = await resp.json();
        if (result.error) {
            status.innerHTML = `<p style="color:var(--danger)">${esc(result.error)}</p>`;
        } else {
            status.innerHTML = `<p style="color:var(--success)">Imported ${result.imported} miners from ${result.report_days}-day report</p>`;
            loadPowerImportStatus();
            loadDashboard();
        }
    } catch (err) {
        status.innerHTML = `<p style="color:var(--danger)">Upload failed: ${err.message}</p>`;
    }
    input.value = '';
}

async function clearPowerData() {
    if (!confirm('Clear all imported power data? Calculations will revert to nameplate wattage.')) return;
    try {
        await fetch('/api/power-import/clear', { method: 'POST' });
        showToast('Imported power data cleared', 'success');
        loadPowerImportStatus();
        loadDashboard();
    } catch (err) {
        showToast('Failed to clear power data', 'error');
    }
}
