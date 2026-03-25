// ---- State ----
let dashboardData = null;
let miners = [];
let locations = [];
let poolData = null;
let uptimeData = null;
let profitHistoryChart = null;
let sortColumn = 'best_profit';
let sortDirection = 'desc';
let expandedMinerId = null;
let autoRefreshInterval = null;
let poolRefreshInterval = null;

// ---- Init ----
document.addEventListener('DOMContentLoaded', () => {
    // Only run full dashboard init on the main page
    var autoRefreshEl = document.getElementById('autoRefreshToggle');
    if (!autoRefreshEl) return;

    loadDashboard();
    loadLocations();
    loadAlgorithms();
    loadPoolStatus();
    loadCoinSwitchAlerts();
    loadProfitHistory();
    loadUptimeStats();
    loadWalletSummary();
    loadSolarMining();
    // Refresh pool status every 2 minutes
    poolRefreshInterval = setInterval(loadPoolStatus, 2 * 60 * 1000);
    // Refresh solar data every 5 minutes
    setInterval(loadSolarMining, 5 * 60 * 1000);

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
        renderSuggestions(dashboardData.suggestions || []);
        updateSourceIndicators(dashboardData.cache_status);
        document.getElementById('lastUpdated').textContent =
            'Updated: ' + new Date(dashboardData.last_updated).toLocaleString();
    } catch (err) {
        showToast('Error loading profitability data. Check if all data sources are reachable.', 'error');
        console.error('Dashboard load error:', err);
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

async function loadPoolStatus() {
    try {
        const resp = await fetch('/api/pool/workers');
        if (!resp.ok) {
            poolData = null;
            return;
        }
        poolData = await resp.json();
        // Re-render table if dashboard data exists to update pool column
        if (dashboardData) renderMinerTable(dashboardData.miners);
        updateFleetHealth();
        // Update PP source indicator
        var ppDot = document.querySelector('.source-dot[data-source="powerpool"]');
        if (ppDot && poolData.configured) {
            var age = poolData.cache_age;
            if (age != null && age < 300) {
                ppDot.className = 'source-dot fresh';
                ppDot.title = 'PowerPool - ' + Math.round(age / 60) + 'm ago';
            } else if (age != null) {
                ppDot.className = 'source-dot stale';
                ppDot.title = 'PowerPool - ' + Math.round(age / 60) + 'm ago';
            }
        }
    } catch (err) {
        console.error('Failed to load pool status', err);
        poolData = null;
    }
}

async function refreshData() {
    const btn = document.getElementById('refreshBtn');
    btn.disabled = true;
    btn.textContent = 'Refreshing...';
    try {
        await fetch('/api/cache/refresh', { method: 'POST' });
        await Promise.all([loadDashboard(), loadPoolStatus()]);
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
    // Backend total_daily_profit already includes solar offset, pool fees, hosting, and demand charge
    var trueDP = summary.total_daily_profit || 0;
    var mp = summary.total_monthly_profit || 0;

    setProfit('totalDailyProfit', trueDP);
    setProfit('totalMonthlyProfit', mp);
    document.getElementById('totalInvestment').textContent = formatCurrency(summary.total_investment);
    document.getElementById('portfolioRoi').textContent =
        summary.portfolio_roi_days > 0 ? `ROI in ${summary.portfolio_roi_days} days` : '--';
    var minerCountsEl = document.getElementById('minerCounts');
    if (minerCountsEl) {
        minerCountsEl.textContent = `${summary.profitable_count} / ${summary.unprofitable_count}`;
        document.getElementById('minerCountsSub').textContent =
            `profitable / unprofitable (${summary.marginal_count} marginal)`;
    }

    // Demand charge display
    var demandEl = document.getElementById('demandChargeCard');
    if (demandEl) {
        if (summary.home_demand_charge > 0) {
            demandEl.style.display = '';
            document.getElementById('demandChargeValue').textContent = formatCurrency(summary.home_demand_charge) + '/mo';
            document.getElementById('demandChargeSub').textContent =
                summary.home_mining_kw.toFixed(1) + ' kW peak \u00D7 $' + summary.demand_rate.toFixed(2) + '/kW';
        } else {
            demandEl.style.display = 'none';
        }
    }

    // Solar savings display (actual or projected 30 days from electricity dashboard)
    var solarEl = document.getElementById('solarSavingsCard');
    if (solarEl) {
        var savings30d = summary.solar_savings_30d || 0;
        var savingsDays = summary.solar_savings_days || 30;
        var hasSolar = summary.total_solar_savings > 0;
        if (hasSolar || savings30d > 0) {
            solarEl.style.display = '';
            document.getElementById('solarDailySavings').textContent = formatCurrency(savings30d);
            if (savingsDays < 30) {
                document.getElementById('solarMonthlySavings').textContent = '~30d (from ' + savingsDays + 'd data)';
            } else {
                document.getElementById('solarMonthlySavings').textContent = 'last 30 days';
            }
        } else {
            solarEl.style.display = 'none';
        }
    }
    updateSummaryStripLayout();
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
        var solarDetail = '';
        if (data.solar_daily_kwh > 0) {
            var offsetPct = Math.round(data.solar_offset_pct * 100);
            solarDetail = ` &middot; <span style="color:var(--success)" title="${data.solar_daily_kwh} kWh/day solar">&#9728; ${offsetPct}% offset</span>`;
        }
        card.innerHTML = `
            <div class="location-info">
                <span class="location-name">${esc(name)}</span>
                <span class="location-detail">${data.units} units &middot; $${data.electricity_cost_kwh}/kWh${solarDetail}</span>
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
        const best = r.best_daily_profit || 0;
        const profitPerKw = getProfitPerKw(r);
        const actualProfit = getActualProfit(r);

        const tr = document.createElement('tr');
        tr.className = r.status === expandedMinerId ? 'expanded' : '';
        tr.onclick = () => openDetailPanel(r);
        var poolCell = renderPoolCell(m.id);

        var actualCell;
        if (actualProfit === 'offline') {
            actualCell = '<span class="pool-offline" style="font-size:0.8rem;">Offline</span>';
        } else if (actualProfit != null) {
            actualCell = `<strong class="${profitClass(actualProfit)}">${formatCurrency(actualProfit)}</strong>`;
        } else {
            actualCell = '<span style="color:var(--text-muted)">--</span>';
        }

        var elecCell;
        var solar = r.solar || {};
        if (solar.daily_electricity != null && solar.offset_pct > 0) {
            var offsetPct = Math.round(solar.offset_pct * 100);
            elecCell = `<span title="Base: ${formatCurrency(r.daily_electricity)} | Solar saves ${formatCurrency(solar.daily_savings)}/day (${offsetPct}% offset)" style="color:var(--success);border-bottom:1px dotted var(--success)">${formatCurrency(solar.daily_electricity)}</span>`;
        } else {
            elecCell = `<span style="color:var(--profit-red)">${formatCurrency(r.daily_electricity)}</span>`;
        }

        // Use solar-adjusted profit for expected if available
        var expectedProfit = (solar.daily_profit != null && solar.offset_pct > 0) ? solar.daily_profit : best;

        tr.innerHTML = `
            <td>${esc(m.name)}</td>
            <td>${m.type}</td>
            <td>${esc(loc.name || '--')}</td>
            <td>${formatWatts(r.power)}</td>
            <td>${m.quantity || 1}</td>
            <td>${elecCell}</td>
            <td class="best-profit ${profitClass(expectedProfit)}"><strong>${formatCurrency(expectedProfit)}</strong></td>
            <td>${actualCell}</td>
            <td class="${profitClass(profitPerKw)}">${formatCurrency(profitPerKw)}</td>
            <td>${r.roi && r.roi.days_to_roi > 0 ? r.roi.days_to_roi + 'd' : '--'}</td>
            <td><span class="status-badge status-${r.status}">${r.status === 'no_data' ? '⚠ No Data' : r.status}</span></td>
            <td>${poolCell}</td>
            <td class="action-btns" onclick="event.stopPropagation()">
                <button class="btn-sm" onclick="editMiner('${m.id}')" title="Edit">&#9998;</button>
                <button class="btn-sm" onclick="duplicateMiner('${m.id}')" title="Duplicate">&#10697;</button>
                <button class="btn-sm delete" onclick="deleteMiner('${m.id}')" title="Delete">&#10005;</button>
            </td>
        `;
        tbody.appendChild(tr);
    });

    // Totals row
    let totalWatts = 0, totalQty = 0, totalElec = 0, totalProfit = 0;
    let totalActualProfit = 0, hasAnyActual = false;
    filtered.forEach(r => {
        const qty = r.miner.quantity || 1;
        totalWatts += (r.power ? r.power.effective_watts || r.power.nameplate_watts : r.miner.wattage || 0) * qty;
        totalQty += qty;
        totalElec += (r.daily_electricity || 0) * qty;
        totalProfit += (r.best_daily_profit || 0) * qty;
        const actual = getActualProfit(r);
        if (typeof actual === 'number') {
            totalActualProfit += actual * qty;
            hasAnyActual = true;
        }
    });
    const totalTr = document.createElement('tr');
    totalTr.className = 'totals-row';
    totalTr.innerHTML = `
        <td colspan="3" style="text-align:right;font-weight:700;">Totals</td>
        <td style="font-weight:700;">${Math.round(totalWatts)}W</td>
        <td style="font-weight:700;">${totalQty}</td>
        <td style="font-weight:700;color:var(--profit-red)">${formatCurrency(totalElec)}</td>
        <td class="best-profit ${profitClass(totalProfit)}" style="font-weight:700;">${formatCurrency(totalProfit)}</td>
        <td class="${hasAnyActual ? profitClass(totalActualProfit) : ''}" style="font-weight:700;">${hasAnyActual ? formatCurrency(totalActualProfit) : '--'}</td>
        <td style="font-weight:700;" class="${profitClass(totalWatts > 0 ? (totalProfit / totalWatts) * 1000 : 0)}">${totalWatts > 0 ? formatCurrency((totalProfit / totalWatts) * 1000) : '--'}</td>
        <td colspan="4"></td>
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
            case 'location': va = a.location.name || ''; vb = b.location.name || ''; break;
            case 'electricity': va = a.daily_electricity || 0; vb = b.daily_electricity || 0; break;
            case 'best_profit': va = a.best_daily_profit || 0; vb = b.best_daily_profit || 0; break;
            case 'actual_profit':
                va = getActualProfit(a);
                vb = getActualProfit(b);
                va = typeof va === 'number' ? va : -99999;
                vb = typeof vb === 'number' ? vb : -99999;
                break;
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
        </div>
    `;
}

function renderROITab(container, result) {
    const roi = result.roi;
    if (!roi) { container.innerHTML = '<p>No ROI data.</p>'; return; }

    const minerId = result.miner.id;
    const hwCost = roi.total_investment || 0;

    // Show basic ROI data first, then fetch enhanced history
    container.innerHTML = `
        <div style="margin-bottom:16px">
            <p>Hardware Cost: <strong>${formatCurrency(hwCost)}</strong></p>
            <p>Daily Profit (current): <strong class="${profitClass(roi.best_daily_profit)}">${formatCurrency(roi.best_daily_profit)}</strong></p>
            <p>Monthly Profit (current): <strong class="${profitClass(roi.best_monthly_profit)}">${formatCurrency(roi.best_monthly_profit)}</strong></p>
            <p>Days to ROI (projected): <strong>${roi.days_to_roi > 0 ? roi.days_to_roi : 'N/A'}</strong></p>
            <p>Payback Date (projected): <strong>${roi.estimated_payback_date || 'N/A'}</strong></p>
            <p>30-Day ROI: <strong>${roi.roi_percentage_30d ? roi.roi_percentage_30d.toFixed(1) + '%' : 'N/A'}</strong></p>
        </div>
        <div id="roiHistorySection" style="border-top:1px solid var(--border);padding-top:12px;">
            <p style="color:var(--text-muted);font-size:0.85rem;">Loading historical ROI data...</p>
        </div>
        <canvas id="roiChart" style="margin-top:12px;"></canvas>
    `;

    // Fetch enhanced ROI history from backend
    fetch('/api/miners/' + minerId + '/roi-history')
        .then(r => r.json())
        .then(data => {
            var section = document.getElementById('roiHistorySection');
            if (!section) return;
            if (!data.days_mining) {
                section.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;">No historical data recorded yet. Profit snapshots are collected hourly.</p>';
                return;
            }

            var bePct = data.breakeven_progress_pct;
            var beDisplay = bePct !== null ? bePct.toFixed(1) + '%' : 'N/A';
            var beBarPct = bePct !== null ? Math.min(Math.max(bePct, 0), 100) : 0;
            var beColor = beBarPct >= 100 ? 'var(--success)' : beBarPct >= 50 ? '#f59e0b' : 'var(--danger)';

            section.innerHTML = `
                <h4 style="font-size:0.9rem;margin-bottom:10px;">Actual ROI Progress</h4>
                <div class="source-grid" style="grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px;">
                    <div class="source-card" style="padding:10px;">
                        <p style="margin:0;">Total Earned</p>
                        <p style="margin:0;"><strong class="profit-positive">${formatCurrency(data.total_earned_to_date)}</strong></p>
                    </div>
                    <div class="source-card" style="padding:10px;">
                        <p style="margin:0;">Total Electricity</p>
                        <p style="margin:0;"><strong class="profit-negative">${formatCurrency(data.total_electricity_to_date)}</strong></p>
                    </div>
                    <div class="source-card" style="padding:10px;">
                        <p style="margin:0;">Net Profit to Date</p>
                        <p style="margin:0;"><strong class="${profitClass(data.net_profit_to_date)}">${formatCurrency(data.net_profit_to_date)}</strong></p>
                    </div>
                    <div class="source-card" style="padding:10px;">
                        <p style="margin:0;">Days Mining</p>
                        <p style="margin:0;"><strong>${data.days_mining}</strong> <span style="color:var(--text-muted);font-size:0.8rem;">(avg ${formatCurrency(data.avg_daily_profit)}/day)</span></p>
                    </div>
                </div>
                ${hwCost > 0 ? `
                <div style="margin-bottom:8px;">
                    <div style="display:flex;justify-content:space-between;font-size:0.85rem;margin-bottom:4px;">
                        <span>Breakeven Progress</span>
                        <span><strong>${beDisplay}</strong></span>
                    </div>
                    <div style="background:var(--bg);border-radius:6px;height:14px;overflow:hidden;">
                        <div style="background:${beColor};height:100%;width:${beBarPct}%;border-radius:6px;transition:width 0.5s;"></div>
                    </div>
                    <div style="display:flex;justify-content:space-between;font-size:0.75rem;color:var(--text-muted);margin-top:4px;">
                        <span>${formatCurrency(data.net_profit_to_date)} earned</span>
                        <span>${formatCurrency(hwCost)} target</span>
                    </div>
                </div>
                <p style="font-size:0.85rem;">Projected Breakeven: <strong>${data.projected_breakeven_date || 'N/A'}</strong></p>
                ` : '<p style="color:var(--text-muted);font-size:0.85rem;">Set a purchase price to see breakeven progress.</p>'}
                <p style="font-size:0.75rem;color:var(--text-muted);margin-top:4px;">Tracking since ${data.first_seen || '--'}</p>
            `;
        })
        .catch(err => {
            var section = document.getElementById('roiHistorySection');
            if (section) section.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;">Could not load ROI history.</p>';
            console.error('ROI history fetch error:', err);
        });

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

function hashToBase(value, unit) {
    if (!value || !unit) return 0;
    const multipliers = {
        'H/s': 1, 'KH/s': 1e3, 'MH/s': 1e6, 'GH/s': 1e9, 'TH/s': 1e12, 'PH/s': 1e15,
        'Sol/s': 1, 'KSol/s': 1e3, 'MSol/s': 1e6, 'GSol/s': 1e9,
    };
    return value * (multipliers[unit] || 1);
}

function getActualProfit(r) {
    if (!poolData || !poolData.statuses) return null;
    var w = poolData.statuses[r.miner.id];
    if (!w) return null;
    if (!w.online) return 'offline';

    var poolHashBase = hashToBase(w.hashrate, w.hashrate_units);
    var ratedHashBase = hashToBase(r.miner.hashrate, r.miner.hashrate_unit);
    if (ratedHashBase <= 0) return null;

    var ratio = poolHashBase / ratedHashBase;
    var grossRevenue = (r.daily_revenue || 0) * ratio;
    // Deduct pool fee
    var poolFeePct = (r.miner.pool_fee_pct || 0) / 100;
    var actualRevenue = grossRevenue * (1 - poolFeePct);
    // Use solar-adjusted electricity if available
    var solar = r.solar || {};
    var elec = (solar.daily_electricity != null && solar.offset_pct > 0) ? solar.daily_electricity : (r.daily_electricity || 0);
    // Deduct hosting fee (monthly / 30)
    var hostingDaily = (r.location && r.location.hosting_fee_monthly || 0) / 30;
    return actualRevenue - elec - hostingDaily;
}

function renderPoolCell(minerId) {
    if (!poolData || !poolData.statuses) return '<span style="color:var(--text-muted);font-size:0.75rem;">--</span>';
    var w = poolData.statuses[minerId];
    if (!w) return '<span style="color:var(--text-muted);font-size:0.75rem;">--</span>';
    var uptimeSuffix = '';
    if (uptimeData && uptimeData[minerId]) {
        var pct = uptimeData[minerId].uptime_pct;
        var uptimeClass = pct >= 99 ? 'profit-positive' : pct >= 95 ? 'profit-neutral' : 'profit-negative';
        uptimeSuffix = '<br><span class="' + uptimeClass + '" style="font-size:0.65rem;">' + pct + '% uptime</span>';
    }
    // Stale share indicator
    var staleSuffix = '';
    if (poolData.stale_analysis && poolData.stale_analysis[minerId]) {
        var sa = poolData.stale_analysis[minerId];
        var staleClass = sa.health === 'good' ? 'profit-positive' : sa.health === 'warning' ? 'profit-neutral' : 'profit-negative';
        staleSuffix = '<br><span class="' + staleClass + '" style="font-size:0.65rem;" title="Share efficiency: ' + sa.share_efficiency + '% | Stale: ' + sa.stale_pct + '% | Est. revenue loss: ' + sa.estimated_revenue_loss_pct + '%">' + sa.share_efficiency + '% eff</span>';
    }
    if (w.online) {
        return '<span class="pool-status pool-online" title="' + esc(w.worker_name) + ' - ' + w.hashrate + ' ' + w.hashrate_units + '">' +
            '<span class="pool-dot online"></span> ' + w.hashrate + ' ' + w.hashrate_units + '</span>' + uptimeSuffix + staleSuffix;
    } else {
        return '<span class="pool-status pool-offline" title="' + esc(w.worker_name) + ' - OFFLINE">' +
            '<span class="pool-dot offline"></span> Offline</span>' + uptimeSuffix;
    }
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

// ---- Fleet Health ----
function updateFleetHealth() {
    const card = document.getElementById('fleetHealthCard');
    if (!poolData || !poolData.statuses || !dashboardData) {
        if (card) card.style.display = 'none';
        return;
    }
    card.style.display = 'block';

    const statuses = Object.values(poolData.statuses);
    const onlineCount = statuses.filter(s => s.online).length;
    const totalMatched = statuses.length;

    document.getElementById('fleetOnlineCount').textContent = `${onlineCount}/${totalMatched} Online`;
    document.getElementById('fleetOnlineCount').className = 'summary-value ' +
        (onlineCount === totalMatched ? 'profit-positive' : onlineCount > 0 ? 'profit-neutral' : 'profit-negative');

    // Hashrate efficiency: sum(actual) / sum(rated) for matched miners
    let totalActual = 0, totalRated = 0;
    dashboardData.miners.forEach(r => {
        const w = poolData.statuses[r.miner.id];
        if (w && w.online) {
            totalActual += hashToBase(w.hashrate, w.hashrate_units);
            totalRated += hashToBase(r.miner.hashrate, r.miner.hashrate_unit);
        }
    });
    const eff = totalRated > 0 ? Math.round(totalActual / totalRated * 100) : 0;
    document.getElementById('fleetHashrateEff').textContent = totalRated > 0 ? `${eff}% hashrate efficiency` : '--';
    updateSummaryStripLayout();
}

function updateSummaryStripLayout() {
    var strip = document.querySelector('.summary-strip');
    if (!strip) return;
    var extraVisible = strip.querySelectorAll('.summary-card[style*="display: none"]').length < strip.querySelectorAll('.summary-card[style]').length;
    // Check if any optional cards are visible
    var optionalCards = ['fleetHealthCard', 'demandChargeCard', 'solarSavingsCard', 'solarMiningCard', 'walletCard'];
    var hasExtra = optionalCards.some(function(id) {
        var el = document.getElementById(id);
        return el && el.style.display !== 'none';
    });
    strip.classList.toggle('has-extra', hasExtra);
}

// ---- Coin Switch Alerts ----
async function loadCoinSwitchAlerts() {
    try {
        const resp = await fetch('/api/alerts/coin-switch');
        if (!resp.ok) return;
        const alerts = await resp.json();
        const banner = document.getElementById('coinSwitchAlerts');
        if (!alerts || alerts.length === 0) {
            banner.style.display = 'none';
            return;
        }
        banner.style.display = 'block';
        banner.innerHTML = alerts.map(a =>
            `<div class="alert-item">
                <span class="alert-icon">&#9888;</span>
                <span>Your <strong>${esc(a.algorithm)}</strong> miners could earn <strong class="profit-positive">+${a.gain_pct}%</strong> more mining <strong>${esc(a.better_coin)}</strong> instead of ${esc(a.current_coin)}</span>
            </div>`
        ).join('');
    } catch (err) {
        console.error('Failed to load coin switch alerts', err);
    }
}

// ---- Profit History Chart ----
async function loadProfitHistory() {
    const section = document.getElementById('historySection');
    try {
        const days = document.getElementById('historyDays')?.value || 30;
        const resp = await fetch(`/api/history/profit?days=${days}`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.fleet_total || data.fleet_total.length === 0) {
            section.style.display = 'none';
            return;
        }
        section.style.display = 'block';
        renderProfitHistoryChart(data);
    } catch (err) {
        console.error('Failed to load profit history', err);
        section.style.display = 'none';
    }
}

function renderProfitHistoryChart(data) {
    const canvas = document.getElementById('profitHistoryChart');
    if (!canvas) return;

    if (profitHistoryChart) profitHistoryChart.destroy();

    const fleet = data.fleet_total;
    const labels = fleet.map(d => d.day);
    const profits = fleet.map(d => d.profit);
    const revenues = fleet.map(d => d.revenue);
    const electricity = fleet.map(d => d.electricity);

    profitHistoryChart = new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Daily Profit',
                    data: profits,
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.3,
                    pointRadius: 3,
                    borderWidth: 2,
                },
                {
                    label: 'Revenue',
                    data: revenues,
                    borderColor: '#6366f1',
                    borderDash: [4, 3],
                    pointRadius: 2,
                    borderWidth: 1.5,
                    fill: false,
                },
                {
                    label: 'Electricity',
                    data: electricity,
                    borderColor: '#ef4444',
                    borderDash: [4, 3],
                    pointRadius: 2,
                    borderWidth: 1.5,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#94a3b8', font: { size: 11 }, boxWidth: 12 },
                },
                tooltip: {
                    callbacks: {
                        label: ctx => ctx.dataset.label + ': $' + ctx.raw.toFixed(2),
                    },
                },
            },
            scales: {
                x: {
                    ticks: { color: '#64748b', maxTicksLimit: 10, font: { size: 10 } },
                    grid: { color: 'rgba(51, 65, 85, 0.3)' },
                },
                y: {
                    ticks: { color: '#64748b', callback: val => '$' + val.toFixed(0), font: { size: 10 } },
                    grid: { color: 'rgba(51, 65, 85, 0.3)' },
                },
            },
            interaction: { intersect: false, mode: 'index' },
        },
    });
}

function toggleHistorySection() {
    const content = document.getElementById('historyContent');
    const icon = document.getElementById('historyToggle');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.classList.remove('collapsed');
    } else {
        content.style.display = 'none';
        icon.classList.add('collapsed');
    }
}

// ---- Uptime Stats ----
async function loadUptimeStats() {
    try {
        const resp = await fetch('/api/history/uptime?days=7');
        if (!resp.ok) return;
        uptimeData = await resp.json();
        // Re-render table to show uptime in pool cells
        if (dashboardData) renderMinerTable(dashboardData.miners);
    } catch (err) {
        console.error('Failed to load uptime stats', err);
    }
}

async function loadWalletSummary() {
    try {
        const resp = await fetch('/api/wallet/portfolio');
        if (!resp.ok) return;
        const data = await resp.json();
        if (data.total_usd > 0) {
            document.getElementById('walletCard').style.display = '';
            document.getElementById('walletTotal').textContent = '$' + data.total_usd.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
            document.getElementById('walletSub').textContent = data.count + ' assets';
        }
    } catch (err) {
        console.error('Failed to load wallet summary', err);
    }
}

function renderSuggestions(suggestions) {
    var container = document.getElementById('suggestionsContainer');
    if (!container) return;
    if (!suggestions || suggestions.length === 0) {
        container.style.display = 'none';
        return;
    }

    // Group relocate suggestions into one
    var grouped = [];
    var relocateMiners = [];
    suggestions.forEach(function (s) {
        if (s.type === 'relocate') {
            relocateMiners.push(s.miner || 'Unknown');
        } else {
            grouped.push(s);
        }
    });
    if (relocateMiners.length > 0) {
        grouped.push({
            priority: 'medium',
            message: relocateMiners.length + ' marginal miner' + (relocateMiners.length > 1 ? 's' : '') +
                ' (' + relocateMiners.join(', ') + ') could save money by moving to a cheaper hosting location.',
        });
    }

    container.style.display = '';
    var countEl = document.getElementById('suggestionsCount');
    if (countEl) {
        var highCount = grouped.filter(function (s) { return s.priority === 'high'; }).length;
        countEl.textContent = highCount > 0 ? highCount + ' alert' + (highCount > 1 ? 's' : '') : grouped.length;
        countEl.className = 'suggestions-badge' + (highCount > 0 ? ' badge-high' : '');
    }

    var html = '';
    grouped.forEach(function (s) {
        var icon, cls;
        switch (s.priority) {
            case 'high': icon = '!'; cls = 'suggestion-high'; break;
            case 'medium': icon = '~'; cls = 'suggestion-medium'; break;
            default: icon = 'i'; cls = 'suggestion-info'; break;
        }
        html += '<div class="suggestion-item ' + cls + '">' +
            '<span class="suggestion-icon">' + icon + '</span>' +
            '<span>' + esc(s.message) + '</span>' +
            '</div>';
    });
    document.getElementById('suggestionsList').innerHTML = html;
}

function toggleSuggestions() {
    var list = document.getElementById('suggestionsList');
    var icon = document.getElementById('suggestionsToggle');
    if (list.style.display === 'none') {
        list.style.display = '';
        icon.className = 'toggle-icon';
    } else {
        list.style.display = 'none';
        icon.className = 'toggle-icon collapsed';
    }
}

async function loadSolarMining() {
    try {
        const resp = await fetch('/api/electricity/solar-mining');
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.connected) return;

        var card = document.getElementById('solarMiningCard');
        card.style.display = '';

        var rt = data.realtime;
        var daily = data.daily;
        var monthly = data.monthly;

        // Main value: net crypto electricity cost after solar
        var el = document.getElementById('solarMiningValue');
        el.textContent = formatCurrency(daily.net_crypto_cost) + '/day';
        el.className = 'summary-value ' + (daily.net_crypto_cost <= 0 ? 'profit-positive' : 'profit-negative');

        // Sub: solar power now + monthly projection
        var solarKw = (rt.solar_w / 1000).toFixed(1);
        var offsetPct = rt.solar_offset_pct.toFixed(0);
        document.getElementById('solarMiningSub').innerHTML =
            '\u2600 ' + solarKw + ' kW now \u00B7 ' + offsetPct + '% offset<br>' +
            formatCurrency(monthly.crypto_solar_savings) + '/mo saved on mining';

        updateSummaryStripLayout();
    } catch (err) {
        console.error('Failed to load solar mining data', err);
    }
}

// ---- Tax Export Modal ----
function openExportModal() {
    document.getElementById('exportModal').style.display = 'flex';
    // Default to year-to-date
    setExportRange('ytd');
}

function closeExportModal() {
    document.getElementById('exportModal').style.display = 'none';
}

function setExportRange(preset) {
    var startEl = document.getElementById('exportStartDate');
    var endEl = document.getElementById('exportEndDate');
    var now = new Date();
    var end = now.toISOString().split('T')[0];
    endEl.value = end;

    switch (preset) {
        case 'ytd':
            startEl.value = now.getFullYear() + '-01-01';
            break;
        case 'lastyear':
            var ly = now.getFullYear() - 1;
            startEl.value = ly + '-01-01';
            endEl.value = ly + '-12-31';
            break;
        case '30d':
            var d30 = new Date(now);
            d30.setDate(d30.getDate() - 30);
            startEl.value = d30.toISOString().split('T')[0];
            break;
        case '90d':
            var d90 = new Date(now);
            d90.setDate(d90.getDate() - 90);
            startEl.value = d90.toISOString().split('T')[0];
            break;
    }
}

function downloadExport(format) {
    var start = document.getElementById('exportStartDate').value;
    var end = document.getElementById('exportEndDate').value;
    if (!start || !end) {
        showToast('Please select both start and end dates', 'error');
        return;
    }
    if (start > end) {
        showToast('Start date must be before end date', 'error');
        return;
    }
    var url = '/api/export/tax/' + format + '?start=' + encodeURIComponent(start) + '&end=' + encodeURIComponent(end);
    window.open(url, '_blank');
}
