// ---- Analysis Tools ----

// Pool fee data (curated, updated periodically)
const POOL_DATA = {
    "SHA-256": [
        { name: "Foundry USA", fee: "0%", payout: "FPPS", minPayout: "0.01 BTC", note: "Largest US pool" },
        { name: "Ocean", fee: "0% + tips", payout: "TIDES", minPayout: "0.0001 BTC", note: "Transparent, non-custodial" },
        { name: "ViaBTC", fee: "1-4%", payout: "PPS+/PPLNS", minPayout: "0.0001 BTC", note: "1% PPLNS, 4% PPS+" },
        { name: "F2Pool", fee: "2.5%", payout: "PPS+", minPayout: "0.005 BTC", note: "Large global pool" },
        { name: "Antpool", fee: "1-4%", payout: "PPS+/PPLNS", minPayout: "0.001 BTC", note: "Bitmain affiliated" },
        { name: "Braiins Pool", fee: "2%", payout: "Score", minPayout: "0.001 BTC", note: "Formerly Slush" },
        { name: "DEMAND", fee: "0%", payout: "FPPS", minPayout: "0.005 BTC", note: "Newer entrant" },
    ],
    "Scrypt": [
        { name: "LitecoinPool", fee: "0%", payout: "PPS", minPayout: "0.01 LTC", note: "Merged LTC+DOGE, no fees" },
        { name: "ViaBTC", fee: "2-4%", payout: "PPS+/PPLNS", minPayout: "0.001 LTC", note: "2% PPLNS, 4% PPS" },
        { name: "F2Pool", fee: "2.5%", payout: "PPS+", minPayout: "0.001 LTC", note: "Merged mining" },
        { name: "Antpool", fee: "1-3%", payout: "PPS+/PPLNS", minPayout: "0.001 LTC", note: "Merged mining" },
        { name: "ProHashing", fee: "1.99%", payout: "PPS", minPayout: "Variable", note: "Auto-switches coins" },
    ],
    "Equihash": [
        { name: "ViaBTC", fee: "2-4%", payout: "PPS+/PPLNS", minPayout: "0.001 ZEC", note: "" },
        { name: "F2Pool", fee: "3%", payout: "PPS+", minPayout: "0.001 ZEC", note: "" },
        { name: "Flypool", fee: "1%", payout: "PPLNS", minPayout: "0.001 ZEC", note: "Ethermine/Bitfly" },
        { name: "2Miners", fee: "1%", payout: "PPLNS", minPayout: "0.01 ZEC", note: "" },
    ],
    "KHeavyHash": [
        { name: "F2Pool", fee: "1%", payout: "PPS+", minPayout: "1 KAS", note: "" },
        { name: "ACC Pool", fee: "0.5%", payout: "PPLNS", minPayout: "10 KAS", note: "" },
        { name: "Kaspium", fee: "1%", payout: "PPLNS", minPayout: "5 KAS", note: "Kaspa-focused" },
        { name: "HeroMiners", fee: "0.9%", payout: "PROP", minPayout: "5 KAS", note: "" },
    ],
    "Etchash": [
        { name: "F2Pool", fee: "1%", payout: "PPS+", minPayout: "0.1 ETC", note: "" },
        { name: "2Miners", fee: "1%", payout: "PPLNS", minPayout: "0.01 ETC", note: "" },
        { name: "Ethermine", fee: "1%", payout: "PPLNS", minPayout: "0.01 ETC", note: "Now Bitfly" },
        { name: "ViaBTC", fee: "2-4%", payout: "PPS+/PPLNS", minPayout: "0.1 ETC", note: "" },
    ],
};

// ---- Price Sensitivity Slider ----
let priceMultiplier = 1.0;

function initPriceSlider() {
    const slider = document.getElementById('priceSlider');
    const label = document.getElementById('priceSliderLabel');
    if (!slider) return;

    slider.addEventListener('input', () => {
        const val = parseInt(slider.value);
        priceMultiplier = val / 100;
        label.textContent = (val >= 0 ? '+' : '') + (val) + '%';
        label.className = 'slider-value ' + (val > 0 ? 'profit-positive' : val < 0 ? 'profit-negative' : '');
        applyPriceScenario();
    });
}

function applyPriceScenario() {
    if (!dashboardData) return;
    const tbody = document.getElementById('scenarioTableBody');
    if (!tbody) return;

    const miners = dashboardData.miners.filter(r => r.status !== 'inactive');
    tbody.innerHTML = '';

    var totalCurrent = 0, totalScenario = 0;
    miners.forEach(r => {
        var m = r.miner;
        var currentRev = r.daily_revenue || 0;
        var scenarioRev = currentRev * priceMultiplier;
        var elec = r.daily_electricity || 0;
        var currentProfit = currentRev - elec;
        var scenarioProfit = scenarioRev - elec;
        var qty = m.quantity || 1;
        totalCurrent += currentProfit * qty;
        totalScenario += scenarioProfit * qty;

        var tr = document.createElement('tr');
        tr.innerHTML =
            '<td>' + esc(m.name) + '</td>' +
            '<td>' + formatCurrency(currentProfit) + '</td>' +
            '<td class="' + profitClass(scenarioProfit) + '"><strong>' + formatCurrency(scenarioProfit) + '</strong></td>' +
            '<td class="' + profitClass(scenarioProfit - currentProfit) + '">' + formatCurrency(scenarioProfit - currentProfit) + '</td>' +
            '<td>' + (scenarioProfit <= 0 ? '<span style="color:var(--danger)">UNPROFITABLE</span>' : '<span style="color:var(--success)">OK</span>') + '</td>';
        tbody.appendChild(tr);
    });

    // Totals
    var totalTr = document.createElement('tr');
    totalTr.className = 'totals-row';
    totalTr.innerHTML =
        '<td style="text-align:right;font-weight:700;">Totals</td>' +
        '<td style="font-weight:700;">' + formatCurrency(totalCurrent) + '</td>' +
        '<td style="font-weight:700;" class="' + profitClass(totalScenario) + '">' + formatCurrency(totalScenario) + '</td>' +
        '<td style="font-weight:700;" class="' + profitClass(totalScenario - totalCurrent) + '">' + formatCurrency(totalScenario - totalCurrent) + '</td>' +
        '<td></td>';
    tbody.appendChild(totalTr);
}

function resetPriceSlider() {
    var slider = document.getElementById('priceSlider');
    if (slider) {
        slider.value = 100;
        priceMultiplier = 1.0;
        document.getElementById('priceSliderLabel').textContent = '+0%';
        document.getElementById('priceSliderLabel').className = 'slider-value';
        applyPriceScenario();
    }
}

// ---- Breakeven Electricity Rate ----
function renderBreakevenTable() {
    if (!dashboardData) return;
    var tbody = document.getElementById('breakevenTableBody');
    if (!tbody) return;

    var miners = [...dashboardData.miners].filter(r => r.status !== 'inactive');
    miners.sort((a, b) => (a.breakeven_elec_rate || 0) - (b.breakeven_elec_rate || 0));

    tbody.innerHTML = '';
    miners.forEach(r => {
        var m = r.miner;
        var loc = r.location;
        var be = r.breakeven_elec_rate || 0;
        var currentRate = loc.electricity_cost_kwh || 0;
        var margin = be - currentRate;
        var marginPct = currentRate > 0 ? ((margin / currentRate) * 100).toFixed(0) : '--';

        var tr = document.createElement('tr');
        tr.innerHTML =
            '<td>' + esc(m.name) + '</td>' +
            '<td>$' + currentRate.toFixed(4) + '</td>' +
            '<td><strong>$' + be.toFixed(4) + '</strong></td>' +
            '<td class="' + (margin > 0.02 ? 'profit-positive' : margin > 0 ? 'profit-neutral' : 'profit-negative') + '">$' + margin.toFixed(4) + ' (' + marginPct + '%)</td>' +
            '<td>' + (margin <= 0 ? '<span style="color:var(--danger)">AT RISK</span>' : margin < 0.02 ? '<span style="color:var(--warning)">TIGHT</span>' : '<span style="color:var(--success)">SAFE</span>') + '</td>';
        tbody.appendChild(tr);
    });
}

// ---- What-If Swap Calculator ----
function populateSwapDropdown() {
    var select = document.getElementById('swapCurrentMiner');
    if (!select || !dashboardData) return;
    select.innerHTML = '<option value="">Select current miner...</option>';
    dashboardData.miners.forEach(r => {
        if (r.status === 'inactive') return;
        var m = r.miner;
        var opt = document.createElement('option');
        opt.value = m.id;
        opt.textContent = m.name + ' (' + m.model + ' - ' + formatCurrency(r.best_daily_profit) + '/day)';
        select.appendChild(opt);
    });
}

async function runSwapComparison() {
    var currentId = document.getElementById('swapCurrentMiner').value;
    var model = document.getElementById('swapRepModel').value;
    var hashrate = parseFloat(document.getElementById('swapRepHashrate').value);
    var unit = document.getElementById('swapRepUnit').value;
    var wattage = parseInt(document.getElementById('swapRepWattage').value);
    var algo = document.getElementById('swapRepAlgo').value;
    var cost = parseFloat(document.getElementById('swapRepCost').value) || 0;
    var resale = parseFloat(document.getElementById('swapResaleValue').value) || 0;

    if (!currentId || !model || !hashrate || !wattage) {
        showToast('Fill in all required fields', 'error');
        return;
    }

    var resultDiv = document.getElementById('swapResult');
    resultDiv.innerHTML = '<p style="color:var(--text-muted)">Calculating...</p>';

    try {
        var resp = await fetch('/api/tools/swap-compare', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                current_miner_id: currentId,
                replacement: {
                    model: model,
                    algorithm: algo,
                    hashrate: hashrate,
                    hashrate_unit: unit,
                    wattage: wattage,
                    purchase_price: cost,
                    resale_current: resale,
                },
            }),
        });
        var data = await resp.json();
        if (data.error) {
            resultDiv.innerHTML = '<p style="color:var(--danger)">' + esc(data.error) + '</p>';
            return;
        }

        var c = data.current;
        var r = data.replacement;
        var cmp = data.comparison;

        resultDiv.innerHTML =
            '<div class="swap-comparison-grid">' +
            '<div class="swap-card">' +
                '<h4 style="color:var(--text-muted)">Current: ' + esc(c.name) + '</h4>' +
                '<p>Revenue: <strong>' + formatCurrency(c.daily_revenue) + '</strong>/day</p>' +
                '<p>Electricity: <strong style="color:var(--profit-red)">' + formatCurrency(c.daily_electricity) + '</strong>/day</p>' +
                '<p>Profit: <strong class="' + profitClass(c.daily_profit) + '">' + formatCurrency(c.daily_profit) + '</strong>/day</p>' +
                '<p>Efficiency: <strong>' + formatCurrency(c.profit_per_kw) + '</strong>/kW</p>' +
                '<p>Wattage: <strong>' + c.wattage + 'W</strong></p>' +
            '</div>' +
            '<div class="swap-card">' +
                '<h4 style="color:var(--primary)">Replacement: ' + esc(r.model) + '</h4>' +
                '<p>Revenue: <strong>' + formatCurrency(r.daily_revenue) + '</strong>/day</p>' +
                '<p>Electricity: <strong style="color:var(--profit-red)">' + formatCurrency(r.daily_electricity) + '</strong>/day</p>' +
                '<p>Profit: <strong class="' + profitClass(r.daily_profit) + '">' + formatCurrency(r.daily_profit) + '</strong>/day</p>' +
                '<p>Efficiency: <strong>' + formatCurrency(r.profit_per_kw) + '</strong>/kW</p>' +
                '<p>Wattage: <strong>' + r.wattage + 'W</strong></p>' +
            '</div>' +
            '<div class="swap-card swap-result-card">' +
                '<h4>Swap Analysis</h4>' +
                '<p>Daily Profit Delta: <strong class="' + profitClass(cmp.profit_delta) + '">' + (cmp.profit_delta >= 0 ? '+' : '') + formatCurrency(cmp.profit_delta) + '</strong></p>' +
                '<p>Monthly Delta: <strong class="' + profitClass(cmp.monthly_delta) + '">' + (cmp.monthly_delta >= 0 ? '+' : '') + formatCurrency(cmp.monthly_delta) + '</strong></p>' +
                '<p>Yearly Delta: <strong class="' + profitClass(cmp.yearly_delta) + '">' + (cmp.yearly_delta >= 0 ? '+' : '') + formatCurrency(cmp.yearly_delta) + '</strong></p>' +
                '<p>Net Cost: <strong>' + formatCurrency(cmp.net_cost) + '</strong> (' + formatCurrency(cmp.replacement_cost) + ' - ' + formatCurrency(cmp.resale_value) + ' resale)</p>' +
                '<p>Breakeven: <strong>' + (cmp.days_to_breakeven > 0 ? cmp.days_to_breakeven + ' days' : cmp.profit_delta <= 0 ? 'Not worth it' : 'Immediate') + '</strong></p>' +
            '</div>' +
            '</div>';
    } catch (err) {
        resultDiv.innerHTML = '<p style="color:var(--danger)">Error: ' + err.message + '</p>';
    }
}

// ---- Pool Fee Comparison ----
function renderPoolComparison() {
    var container = document.getElementById('poolComparisonContent');
    if (!container || !dashboardData) return;

    // Find which algorithms are in use
    var algos = new Set();
    dashboardData.miners.forEach(r => {
        if (r.miner.algorithm) algos.add(r.miner.algorithm);
    });

    // Calculate daily revenue per algo for fee impact
    var revenueByAlgo = {};
    dashboardData.miners.forEach(r => {
        if (r.status === 'inactive') return;
        var algo = r.miner.algorithm;
        if (!revenueByAlgo[algo]) revenueByAlgo[algo] = 0;
        revenueByAlgo[algo] += (r.daily_revenue || 0) * (r.miner.quantity || 1);
    });

    // Build recommendations and tables per algo
    var recommendations = [];
    var html = '';
    algos.forEach(algo => {
        var pools = POOL_DATA[algo];
        if (!pools) return;

        var dailyRev = revenueByAlgo[algo] || 0;

        // Parse fees and find best pool (lowest effective fee)
        var poolsWithFees = pools.map(p => {
            var feeNum = 0;
            var feeMatch = p.fee.match(/(\d+\.?\d*)%/);
            if (feeMatch) feeNum = parseFloat(feeMatch[1]);
            var feeMatch2 = p.fee.match(/(\d+\.?\d*)-(\d+\.?\d*)%/);
            if (feeMatch2) feeNum = parseFloat(feeMatch2[2]);  // worst case for ranges
            var lowestFee = feeNum;
            if (feeMatch2) lowestFee = parseFloat(feeMatch2[1]);  // best case for ranges
            return { pool: p, worstFee: feeNum, bestFee: lowestFee, dailyFee: dailyRev * (feeNum / 100), annualFee: dailyRev * (feeNum / 100) * 365 };
        });

        poolsWithFees.sort((a, b) => a.bestFee - b.bestFee);
        var best = poolsWithFees[0];
        var worst = poolsWithFees[poolsWithFees.length - 1];
        var savings = worst.annualFee - best.annualFee;

        recommendations.push({
            algo: algo,
            bestPool: best.pool.name,
            bestFee: best.pool.fee,
            bestPayout: best.pool.payout,
            annualSaved: savings,
            dailyRev: dailyRev,
        });

        html += '<div class="pool-algo-section">';
        html += '<h4>' + algo + ' <span style="color:var(--text-muted);font-weight:400;font-size:0.85rem;">(' + formatCurrency(dailyRev) + '/day revenue)</span></h4>';
        html += '<table class="coin-table"><thead><tr>' +
            '<th>Pool</th><th>Fee</th><th>Payout</th><th>Min Payout</th><th>Est. Daily Fee Loss</th><th>Est. Annual Fee Loss</th><th>Notes</th>' +
            '</tr></thead><tbody>';

        poolsWithFees.forEach((pf, i) => {
            var p = pf.pool;
            var isBest = i === 0;
            html += '<tr' + (isBest ? ' style="background:rgba(34,197,94,0.08);"' : '') + '>' +
                '<td><strong>' + esc(p.name) + '</strong>' + (isBest ? ' <span style="color:var(--success);font-size:0.75rem;">BEST</span>' : '') + '</td>' +
                '<td>' + esc(p.fee) + '</td>' +
                '<td>' + esc(p.payout) + '</td>' +
                '<td>' + esc(p.minPayout) + '</td>' +
                '<td style="color:var(--profit-red)">' + (pf.dailyFee > 0 ? formatCurrency(pf.dailyFee) : '$0.00') + '</td>' +
                '<td style="color:var(--profit-red)">' + (pf.annualFee > 0 ? formatCurrency(pf.annualFee) : '$0.00') + '</td>' +
                '<td style="color:var(--text-muted);font-size:0.8rem;">' + esc(p.note) + '</td>' +
                '</tr>';
        });
        html += '</tbody></table></div>';
    });

    // Build recommendation banner at the top
    var bannerHtml = '<div class="pool-recommendations">';
    recommendations.forEach(rec => {
        bannerHtml += '<div class="pool-rec-card">' +
            '<div class="pool-rec-algo">' + esc(rec.algo) + '</div>' +
            '<div class="pool-rec-name">' + esc(rec.bestPool) + '</div>' +
            '<div class="pool-rec-detail">Fee: ' + esc(rec.bestFee) + '</div>' +
            (rec.annualSaved > 0 ? '<div class="pool-rec-savings">Save up to <strong class="profit-positive">' + formatCurrency(rec.annualSaved) + '/yr</strong> vs worst pool</div>' : '<div class="pool-rec-savings">Already lowest fee</div>') +
            '</div>';
    });
    bannerHtml += '</div>';

    container.innerHTML = (bannerHtml + html) || '<p style="color:var(--text-muted)">No pool data for your algorithms.</p>';
}

// ---- Power Capacity Optimizer ----
async function runPowerOptimizer() {
    var maxWatts = parseInt(document.getElementById('powerBudgetWatts').value);
    if (!maxWatts || maxWatts <= 0) {
        showToast('Enter a valid wattage budget', 'error');
        return;
    }

    var resultDiv = document.getElementById('powerOptimizerResult');
    resultDiv.innerHTML = '<p style="color:var(--text-muted)">Optimizing...</p>';

    try {
        var resp = await fetch('/api/tools/power-optimize', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ max_watts: maxWatts }),
        });
        var data = await resp.json();
        if (data.error) {
            resultDiv.innerHTML = '<p style="color:var(--danger)">' + esc(data.error) + '</p>';
            return;
        }

        var html = '<div class="optimizer-summary">' +
            '<div class="optimizer-stat"><span class="optimizer-label">Budget</span><span class="optimizer-value">' + data.budget_watts + 'W</span></div>' +
            '<div class="optimizer-stat"><span class="optimizer-label">Used</span><span class="optimizer-value">' + data.used_watts + 'W (' + Math.round(data.used_watts / data.budget_watts * 100) + '%)</span></div>' +
            '<div class="optimizer-stat"><span class="optimizer-label">Remaining</span><span class="optimizer-value">' + data.remaining_watts + 'W</span></div>' +
            '<div class="optimizer-stat"><span class="optimizer-label">Daily Profit</span><span class="optimizer-value profit-positive">' + formatCurrency(data.total_daily_profit) + '</span></div>' +
            '<div class="optimizer-stat"><span class="optimizer-label">Monthly Profit</span><span class="optimizer-value profit-positive">' + formatCurrency(data.total_monthly_profit) + '</span></div>' +
            '</div>';

        if (data.selected.length > 0) {
            html += '<h5 style="margin:12px 0 8px;color:var(--success)">Keep Running (' + data.selected.length + ' units)</h5>';
            html += '<table class="coin-table"><thead><tr><th>Miner</th><th>Model</th><th>Watts</th><th>$/day</th><th>$/kW</th></tr></thead><tbody>';
            data.selected.forEach(c => {
                html += '<tr><td>' + esc(c.name) + '</td><td>' + esc(c.model) + '</td><td>' + c.watts + 'W</td><td class="' + profitClass(c.daily_profit) + '">' + formatCurrency(c.daily_profit) + '</td><td>' + formatCurrency(c.profit_per_kw) + '</td></tr>';
            });
            html += '</tbody></table>';
        }

        if (data.excluded.length > 0) {
            html += '<h5 style="margin:12px 0 8px;color:var(--danger)">Turn Off (' + data.excluded.length + ' units)</h5>';
            html += '<table class="coin-table"><thead><tr><th>Miner</th><th>Model</th><th>Watts</th><th>$/day</th><th>$/kW</th></tr></thead><tbody>';
            data.excluded.forEach(c => {
                html += '<tr style="opacity:0.6"><td>' + esc(c.name) + '</td><td>' + esc(c.model) + '</td><td>' + c.watts + 'W</td><td class="' + profitClass(c.daily_profit) + '">' + formatCurrency(c.daily_profit) + '</td><td>' + formatCurrency(c.profit_per_kw) + '</td></tr>';
            });
            html += '</tbody></table>';
        }

        resultDiv.innerHTML = html;
    } catch (err) {
        resultDiv.innerHTML = '<p style="color:var(--danger)">Error: ' + err.message + '</p>';
    }
}

// ---- Difficulty Trends ----
var difficultyChart = null;

async function loadDifficultyData(algo) {
    var container = document.getElementById('difficultyChartContainer');
    var infoDiv = document.getElementById('difficultyInfo');
    container.innerHTML = '<p style="color:var(--text-muted)">Loading...</p>';

    try {
        var resp = await fetch('/api/tools/difficulty?algo=' + encodeURIComponent(algo));
        var data = await resp.json();

        if (data.data && data.data.length > 0) {
            container.innerHTML = '<canvas id="difficultyCanvas" style="max-height:300px;"></canvas>';
            var ctx = document.getElementById('difficultyCanvas').getContext('2d');

            if (difficultyChart) difficultyChart.destroy();

            var labels = data.data.map(d => new Date(d.timestamp * 1000).toLocaleDateString());
            var values = data.data.map(d => d.difficulty);

            var change = values.length >= 2 ? ((values[values.length - 1] - values[0]) / values[0] * 100).toFixed(1) : 0;

            difficultyChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: labels,
                    datasets: [{
                        label: data.coin + ' Difficulty',
                        data: values,
                        borderColor: '#6366f1',
                        backgroundColor: 'rgba(99, 102, 241, 0.1)',
                        fill: true,
                        tension: 0.3,
                        pointRadius: 0,
                    }],
                },
                options: {
                    responsive: true,
                    plugins: {
                        legend: { display: false },
                    },
                    scales: {
                        x: { ticks: { maxTicksToSkip: 10, color: '#94a3b8' }, grid: { color: '#334155' } },
                        y: { ticks: { color: '#94a3b8', callback: v => (v / 1e12).toFixed(1) + 'T' }, grid: { color: '#334155' } },
                    },
                },
            });

            infoDiv.innerHTML = '<span>180-day change: <strong class="' + (change > 0 ? 'profit-negative' : 'profit-positive') + '">' + (change > 0 ? '+' : '') + change + '%</strong></span>' +
                '<span style="margin-left:16px;">Current: <strong>' + (values[values.length - 1] / 1e12).toFixed(2) + 'T</strong></span>' +
                (change > 10 ? '<span style="margin-left:16px;color:var(--danger)">Difficulty rising fast — profits will decrease</span>' : '');
        } else if (data.current_difficulty) {
            container.innerHTML = '<p>Current ' + esc(data.coin) + ' difficulty: <strong>' + data.current_difficulty.toLocaleString() + '</strong></p>' +
                (data.hashrate_24h ? '<p>24h hashrate: <strong>' + (data.hashrate_24h / 1e12).toFixed(2) + ' TH/s</strong></p>' : '') +
                '<p style="color:var(--text-muted);margin-top:8px;font-size:0.85rem;">Historical chart not available for ' + esc(data.algorithm) + ' on free API.</p>';
            infoDiv.innerHTML = '';
        } else {
            container.innerHTML = '<p style="color:var(--text-muted)">No difficulty data available for ' + esc(algo) + '</p>';
            infoDiv.innerHTML = '';
        }
    } catch (err) {
        container.innerHTML = '<p style="color:var(--danger)">Error: ' + err.message + '</p>';
        infoDiv.innerHTML = '';
    }
}

// ---- Section Toggle ----
function toggleAnalysisSection(sectionId) {
    var content = document.getElementById(sectionId + 'Content');
    var icon = document.getElementById(sectionId + 'Toggle');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.classList.remove('collapsed');
        // Initialize content on first open
        if (sectionId === 'breakeven') renderBreakevenTable();
        if (sectionId === 'scenario') { applyPriceScenario(); initPriceSlider(); }
        if (sectionId === 'pools') renderPoolComparison();
        if (sectionId === 'swap') populateSwapDropdown();
        if (sectionId === 'difficulty') loadDifficultyData('SHA-256');
    } else {
        content.style.display = 'none';
        icon.classList.add('collapsed');
    }
}
