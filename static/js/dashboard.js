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
    loadDashboard();
    loadLocations();
    loadAlgorithms();

    document.getElementById('autoRefreshToggle').addEventListener('change', (e) => {
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

        const tr = document.createElement('tr');
        tr.className = r.status === expandedMinerId ? 'expanded' : '';
        tr.onclick = () => openDetailPanel(r);
        tr.innerHTML = `
            <td>${esc(m.name)}</td>
            <td>${esc(m.model)}</td>
            <td>${m.type}</td>
            <td>${esc(m.algorithm)}</td>
            <td>${esc(loc.name || '--')}</td>
            <td>$${(loc.electricity_cost_kwh || 0).toFixed(2)}</td>
            <td>${m.hashrate} ${m.hashrate_unit}</td>
            <td>${m.wattage}W</td>
            <td>${m.quantity || 1}</td>
            <td class="${profitClass(wtm.daily_profit)}">${wtm.available ? formatCurrency(wtm.daily_profit) : '--'}</td>
            <td class="${profitClass(hrn.daily_profit)}">${hrn.available ? formatCurrency(hrn.daily_profit) : '--'}</td>
            <td>${mn.available ? (mn.rank || '--') : '--'}</td>
            <td class="${profitClass(best)}"><strong>${formatCurrency(best)}</strong></td>
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
            case 'wtm_profit': va = a.sources.whattomine.daily_profit; vb = b.sources.whattomine.daily_profit; break;
            case 'hrn_profit': va = a.sources.hashrateno.daily_profit; vb = b.sources.hashrateno.daily_profit; break;
            case 'mn_rank':
                va = a.sources.miningnow.rank || 9999;
                vb = b.sources.miningnow.rank || 9999;
                break;
            case 'best_profit': va = a.best_daily_profit || 0; vb = b.best_daily_profit || 0; break;
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

    return data.filter(r => {
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

function showToast(message, type = '') {
    const toast = document.createElement('div');
    toast.className = 'toast ' + type;
    toast.textContent = message;
    document.body.appendChild(toast);
    setTimeout(() => toast.remove(), 4000);
}
