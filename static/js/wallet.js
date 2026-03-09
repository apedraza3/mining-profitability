// Wallet page JS
var walletData = null;
var walletSort = { col: 'value', dir: 'desc' };

document.addEventListener('DOMContentLoaded', function () {
    loadWallet();
});

async function loadWallet() {
    try {
        var resp = await fetch('/api/wallet/portfolio');
        if (resp.status === 404) {
            document.getElementById('notConfigured').style.display = 'block';
            document.getElementById('walletSummary').style.display = 'none';
            return;
        }
        if (!resp.ok) throw new Error('Failed to load wallet');
        walletData = await resp.json();
        renderWallet();
    } catch (err) {
        console.error('Wallet load error', err);
        document.getElementById('holdingsCount').textContent = 'Error loading wallet data';
    }
}

function renderWallet() {
    if (!walletData) return;

    // Summary
    document.getElementById('totalPortfolioValue').textContent = formatUSD(walletData.total_usd);
    document.getElementById('holdingsCount').textContent =
        walletData.count + ' asset' + (walletData.count !== 1 ? 's' : '') +
        (walletData.cache_age != null ? ' \u00b7 Updated ' + formatAge(walletData.cache_age) : '');

    // Holdings table
    var section = document.getElementById('holdingsSection');
    section.style.display = 'block';
    renderHoldingsTable();

    // Load mining data for tie-in
    loadMiningEarnings();
}

function renderHoldingsTable() {
    var tbody = document.getElementById('holdingsTableBody');
    var holdings = walletData.holdings.slice();
    var total = walletData.total_usd || 1;

    // Sort
    holdings.sort(function (a, b) {
        var va, vb;
        switch (walletSort.col) {
            case 'currency': va = a.currency; vb = b.currency; break;
            case 'balance': va = a.balance; vb = b.balance; break;
            case 'allocation': va = a.native_balance / total; vb = b.native_balance / total; break;
            default: va = a.native_balance; vb = b.native_balance;
        }
        if (typeof va === 'string') {
            return walletSort.dir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return walletSort.dir === 'asc' ? va - vb : vb - va;
    });

    tbody.innerHTML = '';
    holdings.forEach(function (h) {
        var pct = total > 0 ? (h.native_balance / total * 100) : 0;
        var tr = document.createElement('tr');
        tr.innerHTML =
            '<td><strong>' + esc(h.currency) + '</strong> <span style="color:var(--text-muted);font-size:0.8rem;">' + esc(h.currency_name) + '</span></td>' +
            '<td>' + formatBalance(h.balance, h.currency) + '</td>' +
            '<td><strong>' + formatUSD(h.native_balance) + '</strong></td>' +
            '<td>' +
                '<div style="display:flex;align-items:center;gap:8px;">' +
                    '<div class="alloc-bar"><div class="alloc-fill" style="width:' + Math.min(pct, 100) + '%"></div></div>' +
                    '<span style="font-size:0.8rem;">' + pct.toFixed(1) + '%</span>' +
                '</div>' +
            '</td>' +
            '<td style="color:var(--text-muted);font-size:0.8rem;">' + esc(h.type) + '</td>';
        tbody.appendChild(tr);
    });

    // Update sort indicators
    document.querySelectorAll('#holdingsSection th').forEach(function (th) {
        th.classList.remove('sort-asc', 'sort-desc');
        if (th.dataset.col === walletSort.col) {
            th.classList.add(walletSort.dir === 'asc' ? 'sort-asc' : 'sort-desc');
        }
    });
}

function sortWallet(col) {
    if (walletSort.col === col) {
        walletSort.dir = walletSort.dir === 'asc' ? 'desc' : 'asc';
    } else {
        walletSort.col = col;
        walletSort.dir = col === 'currency' ? 'asc' : 'desc';
    }
    renderHoldingsTable();
}

async function loadMiningEarnings() {
    try {
        var resp = await fetch('/api/pool/revenue');
        if (!resp.ok) return;
        var revenue = await resp.json();
        if (!revenue || !revenue.algorithms) return;

        var section = document.getElementById('earningsSection');
        var grid = document.getElementById('earningsGrid');
        section.style.display = 'block';
        grid.innerHTML = '';

        // Map mining earnings to wallet holdings
        var algos = revenue.algorithms || {};
        for (var algo in algos) {
            var algoData = algos[algo];
            var coin = algoData.coin || algo;
            // Find matching holding
            var holding = walletData.holdings.find(function (h) {
                return h.currency.toUpperCase() === coin.toUpperCase();
            });

            var card = document.createElement('div');
            card.className = 'wallet-earnings-card';
            var holdingInfo = holding
                ? '<div class="earnings-detail">Wallet: <strong>' + formatBalance(holding.balance, holding.currency) + '</strong> (' + formatUSD(holding.native_balance) + ')</div>'
                : '<div class="earnings-detail" style="color:var(--text-muted)">Not in wallet</div>';

            card.innerHTML =
                '<div class="earnings-coin">' + esc(coin) + '</div>' +
                '<div class="earnings-detail">Unpaid: ' + formatSmall(algoData.unpaid_balance || 0) + ' ' + esc(coin) + '</div>' +
                '<div class="earnings-detail">24h: ' + formatSmall(algoData.estimated_24h || 0) + ' ' + esc(coin) + '</div>' +
                holdingInfo;
            grid.appendChild(card);
        }
    } catch (err) {
        // Mining data not available — that's fine, just hide the section
    }
}

function toggleEarningsSection() {
    var content = document.getElementById('earningsContent');
    var icon = document.getElementById('earningsToggle');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.classList.remove('collapsed');
    } else {
        content.style.display = 'none';
        icon.classList.add('collapsed');
    }
}

// Helpers
function formatUSD(val) {
    if (val == null) return '--';
    return '$' + Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatBalance(val, currency) {
    if (val == null) return '--';
    // Show more decimals for small-unit coins
    var decimals = val < 0.01 ? 8 : val < 1 ? 6 : val < 100 ? 4 : 2;
    return Number(val).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: decimals });
}

function formatSmall(val) {
    if (val == null) return '0';
    if (val < 0.00001) return val.toExponential(2);
    return Number(val).toLocaleString('en-US', { maximumFractionDigits: 8 });
}

function formatAge(seconds) {
    if (seconds == null) return '';
    if (seconds < 60) return seconds + 's ago';
    if (seconds < 3600) return Math.round(seconds / 60) + 'm ago';
    return Math.floor(seconds / 3600) + 'h ago';
}

function esc(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
