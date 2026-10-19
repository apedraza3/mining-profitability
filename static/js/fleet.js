// Fleet Now page. All text from sources is inserted with textContent (worker names
// come from third parties and must never be treated as HTML).
(function () {
    var REFRESH_MS = 60000;

    function el(tag, opts, children) {
        var node = document.createElement(tag);
        opts = opts || {};
        if (opts.cls) node.className = opts.cls;
        if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
        if (opts.title) node.title = opts.title;
        (children || []).forEach(function (c) { if (c) node.appendChild(c); });
        return node;
    }

    function money(v) {
        if (v === null || v === undefined) return '—';
        var sign = v < 0 ? '-' : '';
        return sign + '$' + Math.abs(v).toFixed(2);
    }

    function moneyCell(v, title) {
        var cls = 'num';
        if (v !== null && v !== undefined) cls += v < 0 ? ' neg' : ' pos';
        return el('td', { cls: cls, text: money(v), title: title });
    }

    function hashrate(hs) {
        if (hs === null || hs === undefined) return '—';
        var units = [['E', 1e18], ['P', 1e15], ['T', 1e12], ['G', 1e9], ['M', 1e6], ['K', 1e3]];
        for (var i = 0; i < units.length; i++) {
            if (hs >= units[i][1]) return (hs / units[i][1]).toFixed(2) + ' ' + units[i][0] + 'H/s';
        }
        return hs.toFixed(0) + ' H/s';
    }

    function ago(iso) {
        if (!iso) return 'never';
        var s = (Date.now() - new Date(iso).getTime()) / 1000;
        if (s < 90) return Math.round(s) + 's ago';
        if (s < 5400) return Math.round(s / 60) + 'm ago';
        if (s < 172800) return Math.round(s / 3600) + 'h ago';
        return Math.round(s / 86400) + 'd ago';
    }

    function statusClass(r) {
        if (r.running === true) return 'st-ok';
        if (r.running === false) return 'st-bad';
        return 'st-unknown';
    }

    function renderSources(sources) {
        var box = document.getElementById('fleetSources');
        box.textContent = '';
        if (!sources.length) {
            box.appendChild(el('span', { cls: 'muted', text: 'The fleet poller has not run yet.' }));
            return;
        }
        sources.forEach(function (s) {
            var title = s.state === 'error' ? (s.last_error || '') :
                (s.last_success ? 'Last success ' + ago(s.last_success) : '');
            box.appendChild(el('span', { cls: 'src-chip src-' + s.state.replace(/ /g, '-'), title: title },
                [el('strong', { text: s.source }), el('span', { text: ' ' + s.state })]));
        });
    }

    function renderSummary(sm) {
        var box = document.getElementById('fleetSummary');
        box.textContent = '';
        function card(label, value, sub, cls) {
            box.appendChild(el('div', { cls: 'fleet-card' }, [
                el('div', { cls: 'fleet-card-label', text: label }),
                el('div', { cls: 'fleet-card-value ' + (cls || ''), text: value }),
                sub ? el('div', { cls: 'fleet-card-sub', text: sub }) : null
            ]));
        }
        card('Machines', sm.running + ' running', sm.not_running + ' stopped, ' + sm.unknown + ' unknown');
        card('Expected', money(sm.expected_usd_day) + '/day', 'pool pay rate x hashrate');
        card('Power', money(sm.power_usd_day) + '/day',
            sm.home_rate_kwh ? 'home $' + sm.home_rate_kwh.toFixed(4) + '/kWh' : 'home rate unavailable');
        card('Demand share', money(sm.demand_usd_day) + '/day',
            sm.demand_rate_kw_month ? '$' + sm.demand_rate_kw_month.toFixed(2) + '/kW-month' : '');
        var net = sm.net_loaded_usd_day;
        card('Net incl. demand', money(net) + '/day',
            'before demand ' + money(sm.net_marginal_usd_day), net !== null && net < 0 ? 'neg' : 'pos');
        if (sm.rows_missing_cost) {
            card('Missing cost', String(sm.rows_missing_cost), 'machines without watts or rate', 'warn');
        }
    }

    function renderRows(rows) {
        var body = document.getElementById('fleetRows');
        body.textContent = '';
        if (!rows.length) {
            body.appendChild(el('tr', {}, [el('td', { cls: 'muted', text: 'No machines reported yet. Configure a source (see fleet.env.example).' })]));
            body.firstChild.firstChild.colSpan = 9;
            return;
        }
        rows.forEach(function (r) {
            var nameCell = el('td', {}, [
                el('div', { cls: 'fleet-name', text: r.name }),
                el('div', { cls: 'fleet-meta', text: [r.model, r.location, r.sources.join(' + ')].filter(Boolean).join(' · ') }),
                r.in_inventory ? null : el('div', { cls: 'fleet-flag', text: 'not in inventory' }),
                r.stale_sources.length ? el('div', { cls: 'fleet-flag', text: 'stale: ' + r.stale_sources.join(', ') }) : null
            ]);
            var statusCell = el('td', {}, [el('span', { cls: 'st ' + statusClass(r), text: r.status || 'unknown' })]);
            var hr = el('td', { cls: 'num', text: hashrate(r.hashrate_hs),
                title: r.device_hashrate_hs ? 'device reports ' + hashrate(r.device_hashrate_hs) : '' });
            var watts = el('td', { cls: 'num', text: r.watts === null ? '—' : Math.round(r.watts) + ' W',
                title: r.watts_quality || '' });
            if (r.watts_quality && r.watts_quality.indexOf('estimated') === 0) watts.classList.add('est');
            body.appendChild(el('tr', {}, [
                nameCell, statusCell, hr, watts,
                moneyCell(r.expected_usd_day, r.expected_basis || ''),
                moneyCell(r.power_usd_day === null ? null : -r.power_usd_day,
                    r.rate_kwh ? '$' + r.rate_kwh + '/kWh' : 'no rate'),
                moneyCell(r.demand_usd_day === null ? null : -r.demand_usd_day, 'home demand share'),
                moneyCell(r.net_marginal_usd_day, 'expected - power'),
                moneyCell(r.net_loaded_usd_day, 'expected - power - demand share')
            ]));
        });
    }

    function renderAccounts(accounts) {
        var box = document.getElementById('fleetAccounts');
        box.textContent = '';
        if (!accounts.length) {
            box.appendChild(el('span', { cls: 'muted', text: 'No account data yet.' }));
            return;
        }
        accounts.forEach(function (a) {
            var lines = [];
            if (a.source === 'kryptex') {
                var b = a.data.balance || {};
                lines.push('Balance: ' + (b.total === undefined ? '—' : b.total) + ' ' + (a.data.coin || '').toUpperCase());
                lines.push('Actual last 7 days: ' + (a.actual_last_7d_coin || 0) + ' ' + (a.data.coin || '').toUpperCase() +
                    (a.actual_last_7d_usd !== null && a.actual_last_7d_usd !== undefined ? ' (' + money(a.actual_last_7d_usd) + ')' : ''));
            } else if (a.source === 'clore') {
                (a.data.wallets || []).forEach(function (w) { lines.push(w.name + ': ' + w.balance); });
            } else {
                lines.push(JSON.stringify(a.data.raw !== undefined ? a.data.raw : a.data).slice(0, 300));
                if (a.data.note) lines.push(a.data.note);
            }
            box.appendChild(el('div', { cls: 'fleet-account' }, [
                el('div', { cls: 'fleet-name', text: a.account }),
                el('div', { cls: 'fleet-meta', text: 'updated ' + ago(a.fetched_at) })
            ].concat(lines.map(function (l) { return el('div', { cls: 'fleet-line', text: l }); }))));
        });
    }

    function load() {
        fetch('/api/fleet')
            .then(function (r) { return r.json(); })
            .then(function (d) {
                renderSources(d.sources || []);
                renderSummary(d.summary || {});
                renderRows(d.machines || []);
                renderAccounts(d.accounts || []);
                document.getElementById('fleetUpdated').textContent = 'Updated ' + new Date().toLocaleTimeString() +
                    (d.summary && d.summary.btc_usd ? ' · BTC $' + Math.round(d.summary.btc_usd).toLocaleString() : '');
            })
            .catch(function (e) {
                document.getElementById('fleetUpdated').textContent = 'Failed to load: ' + e.message;
            });
    }

    load();
    setInterval(load, REFRESH_MS);
})();
