// Solar Loan Analysis Page

(function() {
    'use strict';

    var analysisData = null;

    function formatCurrency(val) {
        if (val === null || val === undefined) return '$0.00';
        var abs = Math.abs(val);
        var str = '$' + abs.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        return val < 0 ? '-' + str : str;
    }

    function formatWatts(w) {
        if (w === null || w === undefined) return '-- W';
        if (Math.abs(w) >= 1000) return (w / 1000).toFixed(1) + ' kW';
        return Math.round(w) + ' W';
    }

    // Section toggle
    window.toggleSection = function(section) {
        var content = document.getElementById(section + 'Content');
        var toggle = document.getElementById(section + 'Toggle');
        if (!content) return;
        var hidden = content.style.display === 'none';
        content.style.display = hidden ? '' : 'none';
        if (toggle) toggle.classList.toggle('collapsed', !hidden);
    };

    // Load analysis data
    function loadAnalysis() {
        fetch('/api/solar-loan/analysis')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                analysisData = data;
                renderAll(data);
            })
            .catch(function(err) {
                console.error('Solar loan analysis error:', err);
                document.getElementById('connectionStatus').innerHTML =
                    '<div class="suggestion-item suggestion-high"><div class="suggestion-icon">!</div> Failed to load analysis data.</div>';
            });
    }

    function renderAll(data) {
        renderConnectionStatus(data);
        renderVerdict(data);
        renderMonthly(data);
        renderAnnual(data);
        renderLoanProgress(data);
        renderPostLoan(data);
        renderLiveData(data);
        loadSettings();
    }

    function renderConnectionStatus(data) {
        var el = document.getElementById('connectionStatus');
        if (!data.electricity_connected) {
            el.innerHTML = '<div class="suggestion-item suggestion-medium"><div class="suggestion-icon">!</div> Electricity dashboard not connected. Savings data is estimated.</div>';
        } else {
            el.innerHTML = '';
        }
    }

    function renderVerdict(data) {
        var banner = document.getElementById('verdictBanner');
        var icon = document.getElementById('verdictIcon');
        var title = document.getElementById('verdictTitle');
        var detail = document.getElementById('verdictDetail');
        var m = data.monthly || {};

        banner.style.display = 'flex';

        if (m.is_profitable) {
            banner.className = 'solar-verdict-banner verdict-positive';
            icon.textContent = '+';
            title.textContent = 'Solar is paying for itself';
            detail.textContent = 'You\'re saving ' + formatCurrency(m.net) + '/mo more than your loan payment.';
        } else if (m.net >= -50) {
            banner.className = 'solar-verdict-banner verdict-neutral';
            icon.textContent = '~';
            title.textContent = 'Solar is nearly breaking even';
            detail.textContent = 'You\'re ' + formatCurrency(Math.abs(m.net)) + '/mo short of covering your loan. Close to breakeven.';
        } else {
            banner.className = 'solar-verdict-banner verdict-negative';
            icon.textContent = '-';
            title.textContent = 'Solar is costing you ' + formatCurrency(Math.abs(m.net)) + '/mo';
            var remaining = data.loan_progress || {};
            if (remaining.remaining_years > 0) {
                detail.textContent = 'But the loan ends in ~' + remaining.remaining_years + ' years. After that, it\'s pure savings of ' +
                    formatCurrency(data.post_loan.monthly_benefit) + '/mo.';
            } else {
                detail.textContent = 'Your loan payment exceeds the energy savings at your current electricity rate.';
            }
        }
    }

    function renderMonthly(data) {
        var m = data.monthly || {};
        document.getElementById('loanPayment').textContent = formatCurrency(m.payment);
        document.getElementById('energySavings').textContent = formatCurrency(m.energy_savings);
        document.getElementById('demandSavings').textContent = formatCurrency(m.demand_savings);

        var netEl = document.getElementById('netMonthly');
        var netSubEl = document.getElementById('netMonthlySub');
        netEl.textContent = formatCurrency(m.net);
        netEl.className = 'solar-card-value ' + (m.net >= 0 ? 'profit-positive' : 'profit-negative');

        if (m.net >= 0) {
            netSubEl.textContent = 'solar covers your loan + more';
        } else {
            netSubEl.textContent = 'monthly shortfall';
        }
    }

    function renderAnnual(data) {
        var a = data.annual || {};
        document.getElementById('annualCost').textContent = formatCurrency(a.cost);
        document.getElementById('annualSavings').textContent = formatCurrency(a.savings);

        var netEl = document.getElementById('annualNet');
        netEl.textContent = formatCurrency(a.net);
        netEl.className = 'solar-annual-value ' + (a.net >= 0 ? 'profit-positive' : 'profit-negative');
    }

    function renderLoanProgress(data) {
        var lp = data.loan_progress || {};
        var loan = data.loan || {};

        var outstanding = lp.outstanding || 0;
        var original = loan.original_loan_amount || 0;
        var paid = original > 0 ? original - outstanding : 0;
        var pct = original > 0 ? Math.min((paid / original) * 100, 100) : 0;

        document.getElementById('loanProgressFill').style.width = pct + '%';
        document.getElementById('loanPaidLabel').textContent = original > 0 ? formatCurrency(paid) + ' paid' : 'Set original loan amount in settings';
        document.getElementById('loanRemainingLabel').textContent = formatCurrency(outstanding) + ' remaining';

        document.getElementById('outstandingPrincipal').textContent = formatCurrency(outstanding);
        document.getElementById('monthlyPaymentStat').textContent = formatCurrency(data.monthly.payment);

        if (lp.remaining_years > 0) {
            var yrs = Math.floor(lp.remaining_years);
            var mos = Math.round((lp.remaining_years - yrs) * 12);
            document.getElementById('remainingTime').textContent = yrs + 'y ' + mos + 'mo';
        } else {
            document.getElementById('remainingTime').textContent = '--';
        }
    }

    function renderPostLoan(data) {
        var pl = data.post_loan || {};
        var lt = data.lifetime || {};

        document.getElementById('postLoanMonthly').textContent = formatCurrency(pl.monthly_benefit);
        document.getElementById('postLoanAnnual').textContent = formatCurrency(pl.annual_benefit);
        document.getElementById('remainingLife').textContent = lt.remaining_life_years > 0 ? lt.remaining_life_years + ' years' : '--';

        var ltVal = document.getElementById('lifetimeValue');
        ltVal.textContent = formatCurrency(lt.total_value);
        ltVal.className = 'solar-card-value ' + (lt.total_value >= 0 ? 'profit-positive' : 'profit-negative');
    }

    function renderLiveData(data) {
        var rt = data.realtime || {};
        document.getElementById('liveSolarW').textContent = formatWatts(rt.solar_w);
        document.getElementById('liveConsW').textContent = formatWatts(rt.consumption_w);
        document.getElementById('liveCryptoW').textContent = formatWatts(rt.crypto_w);
        document.getElementById('liveGridW').textContent = formatWatts(rt.net_grid_w);
        document.getElementById('liveEnergyRate').textContent = data.energy_rate ? '$' + data.energy_rate.toFixed(4) + '/kWh' : '--';

        var roi = data.solar_roi || {};
        document.getElementById('liveSolarRoi').textContent = roi.payback_pct ? roi.payback_pct.toFixed(1) + '%' : '--';
    }

    // Settings form
    function loadSettings() {
        fetch('/api/solar-loan')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var loan = data.loan || {};
                var sys = data.system || {};
                document.getElementById('fldMonthlyPayment').value = loan.monthly_payment || '';
                document.getElementById('fldOutstandingPrincipal').value = loan.outstanding_principal || '';
                document.getElementById('fldOriginalLoan').value = loan.original_loan_amount || '';
                document.getElementById('fldInterestRate').value = loan.interest_rate || '';
                document.getElementById('fldLoanTerm').value = loan.loan_term_months || '';
                document.getElementById('fldLender').value = loan.lender || '';
                document.getElementById('fldLoanStartDate').value = loan.start_date || '';
                document.getElementById('fldSystemCost').value = sys.system_cost || '';
                document.getElementById('fldSystemSize').value = sys.system_size_kw || '';
                document.getElementById('fldPanelCount').value = sys.panel_count || '';
                document.getElementById('fldInstallDate').value = sys.install_date || '';
                document.getElementById('fldItcPct').value = sys.federal_itc_pct || 30;
                document.getElementById('fldIncentives').value = sys.incentives || '';
            });
    }

    window.saveSolarLoan = function(e) {
        e.preventDefault();
        var payload = {
            loan: {
                monthly_payment: parseFloat(document.getElementById('fldMonthlyPayment').value) || 0,
                outstanding_principal: parseFloat(document.getElementById('fldOutstandingPrincipal').value) || 0,
                original_loan_amount: parseFloat(document.getElementById('fldOriginalLoan').value) || 0,
                interest_rate: parseFloat(document.getElementById('fldInterestRate').value) || 0,
                loan_term_months: parseInt(document.getElementById('fldLoanTerm').value) || 0,
                lender: document.getElementById('fldLender').value.trim(),
                start_date: document.getElementById('fldLoanStartDate').value,
            },
            system: {
                system_cost: parseFloat(document.getElementById('fldSystemCost').value) || 0,
                system_size_kw: parseFloat(document.getElementById('fldSystemSize').value) || 0,
                panel_count: parseInt(document.getElementById('fldPanelCount').value) || 0,
                install_date: document.getElementById('fldInstallDate').value,
                federal_itc_pct: parseInt(document.getElementById('fldItcPct').value) || 30,
                incentives: parseFloat(document.getElementById('fldIncentives').value) || 0,
            }
        };

        fetch('/api/solar-loan', {
            method: 'PUT',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        })
        .then(function(r) { return r.json(); })
        .then(function() {
            showToast('Settings saved. Refreshing analysis...');
            loadAnalysis();
        })
        .catch(function(err) {
            showToast('Error saving settings: ' + err, true);
        });
    };

    function showToast(msg, isError) {
        var toast = document.createElement('div');
        toast.className = 'toast' + (isError ? ' error' : ' success');
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(function() { toast.remove(); }, 3000);
    }

    // Init
    loadAnalysis();
})();
