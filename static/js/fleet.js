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
        card('Expected', money(sm.expected_usd_day) + '/day',
            sm.rows_no_live_source ? 'includes ' + sm.rows_no_live_source + ' estimated (no live source)'
                                   : 'pool pay rate x hashrate');
        card('Power', money(sm.power_usd_day) + '/day',
            sm.home_rate_kwh ? 'home $' + sm.home_rate_kwh.toFixed(4) + '/kWh' : 'home rate unavailable');
        card('Demand share', money(sm.demand_usd_day) + '/day',
            sm.demand_rate_kw_month ? '$' + sm.demand_rate_kw_month.toFixed(2) + '/kW-month' : '');
        var net = sm.net_loaded_usd_day;
        card('Net incl. demand', money(net) + '/day',
            'before demand ' + money(sm.net_marginal_usd_day) +
            (sm.rows_missing_cost ? ' (only machines with a known cost)' : ''),
            net !== null && net < 0 ? 'neg' : 'pos');
        if (sm.rows_missing_cost) {
            card('Missing cost', String(sm.rows_missing_cost), 'machines without watts or rate', 'warn');
        }
        var hp = sm.home_power;
        if (hp && hp.measured_w !== null && hp.measured_w !== undefined) {
            var kw = function (w) { return (w / 1000).toFixed(2) + ' kW'; };
            var sub = 'Emporia crypto circuits vs ' + kw(hp.accounted_w) + ' from known machines';
            var cls = '';
            if (hp.mode === 'overhead') {
                sub = 'miners ' + kw(hp.accounted_w) + (hp.loads || []).map(function (l) {
                    return ' + ' + l.label + ' ' + kw(l.watts) + (l.mining ? '' : ' (not mining)');
                }).join('') + (hp.unattributed_w > 50 ? ' + ' + kw(hp.unattributed_w) + ' unexplained' : '');
                if (hp.unattributed_w > 50) cls = 'warn';
            } else if (hp.mode === 'allocated') {
                sub = 'matches known machines; their watts are now allocated from the meter';
            } else if (hp.mode === 'unattributed gap') {
                sub = kw(hp.gap_w) + ' not matched to any machine' +
                    (hp.assumed_off && hp.assumed_off.length
                        ? ' (still powered? ' + hp.assumed_off.join(', ') + ' report no hashrate)' : '');
                cls = 'warn';
            } else if (hp.mode === 'meter stale') {
                sub = 'meter reading is stale (' + ago(hp.measured_at) + ')';
                cls = 'warn';
            } else if (hp.mode === 'meter below estimates') {
                sub = 'meter is below nameplate estimates (' + kw(hp.accounted_w) + ')';
            }
            card(hp.non_mining_w ? 'Crypto sub-panel, measured' : 'Home mining, measured',
                kw(hp.measured_w), sub, cls);
        }
        (sm.pdus || []).forEach(function (p) {
            if (p.error) {
                card('PDU ' + p.name, 'no reading', p.host + ': ' + p.error, 'warn');
                return;
            }
            var unit = p.power_kind && p.power_kind.indexOf('VA') >= 0 ? ' kVA' : ' kW';
            var parts = (p.branches || []).map(function (b) {
                var v = b.watts !== undefined ? b.watts : b.va;
                return b.branch + ' ' + (v / 1000).toFixed(2) + (b.amps !== null && b.amps !== undefined ? ' (' + b.amps.toFixed(1) + ' A)' : '');
            });
            if (p.peak_w) parts.push('peak ' + (p.peak_w / 1000).toFixed(2) + ' kW');
            card('PDU ' + p.name, p.total_w === null ? '—' : (p.total_w / 1000).toFixed(2) + unit,
                (parts.join(' · ') || p.host) + ' · ' + ago(p.fetched_at));
        });
        var cl = sm.climate;
        if (cl && cl.temp_f !== null && cl.temp_f !== undefined) {
            // The probe hangs in the hot aisle, so its RH reads low just because the air is hot.
            // Dew point is the same everywhere in the room: < ~32 F = dry (static), > ~55 F = damp.
            var dp = cl.dew_point_f;
            var dryness = dp === null || dp === undefined ? '' :
                dp < 32 ? ' (dry air: static risk)' : dp > 55 ? ' (damp air: condensation risk)' : '';
            var airCls = (cl.temp_f >= 105 || dryness) ? 'warn' : '';
            var fanText = (cl.fans || []).map(function (f) {
                return f.label + ' ' + (f.online ? (f.speed === null ? '?' : f.speed) + '/10' : 'offline') +
                    (f.mode && f.mode !== 'On' ? ' (' + f.mode + ')' : '');
            }).join(' · ');
            card(cl.label || 'Hot aisle', cl.temp_f.toFixed(1) + ' °F',
                (cl.humidity_pct === null ? '' : cl.humidity_pct.toFixed(0) + '% RH') +
                (dp !== null && dp !== undefined ? ', dew point ' + dp.toFixed(0) + ' °F' : '') + dryness +
                (fanText ? ' · fans ' + fanText : '') + ' · ' + ago(cl.fetched_at), airCls);
        }
        var al = sm.alerts;
        if (al) {
            var act = al.active || [];
            card('Alerts', act.length ? act.length + ' active' : 'all clear',
                (act.length ? act.map(function (a) { return a.message; }).join(' · ') + ' · ' : '') +
                (al.channels && al.channels.length ? 'sending to ' + al.channels.join(', ')
                    : 'no channel yet: set Telegram or Discord in the dashboard Alert settings') +
                (al.send_errors && al.send_errors.length ? ' · send failed: ' + al.send_errors.join('; ') : ''),
                act.length || (al.send_errors && al.send_errors.length) ? 'warn' : '');
        }
        var fc = sm.fan_control;
        if (fc && fc.mode && fc.mode !== 'off') {
            var drv = fc.driver ? fc.driver.name + ' ' + fc.driver.value + (fc.driver.unit || '') : 'no data';
            var label = fc.mode === 'on' ? 'Fan control' : 'Fan control (dry run)';
            var value = (fc.current === null || fc.current === undefined ? '?' : fc.current) + ' → ' + fc.target + '/10';
            var inputs = (fc.inputs || []).map(function (i) {
                return i.name + ' ' + i.value + (i.unit || '') + (i.stale ? ' (stale)' : '');
            }).join(' · ');
            card(label, value, 'driven by ' + drv + ': ' + fc.reason +
                (fc.mode !== 'on' ? ' · not changing the fans until FAN_CONTROL=on' : '') +
                (fc.skipped_ports && fc.skipped_ports.length ? ' · ports ' + fc.skipped_ports.join(', ') + ' left alone (not in On mode)' : '') +
                (fc.error ? ' · last write failed: ' + fc.error : '') + ' · inputs: ' + inputs,
                fc.error ? 'warn' : '');
        }
        var circuits = sm.pdu_circuits || [];
        if (circuits.length) {
            var applied = circuits.filter(function (c) { return c.state === 'applied'; }).length;
            card('PDU circuits', applied + ' of ' + circuits.length + ' measured',
                circuits.map(function (c) {
                    var w = c.watts === null ? '—' : (c.watts / 1000).toFixed(2) + (c.unit === 'VA' ? ' kVA' : ' kW');
                    return c.circuit + ' ' + w + ' → ' + (c.state === 'applied' ? c.applied_to.join(', ') : c.state);
                }).join(' · '),
                applied < circuits.length ? 'warn' : '');
        }
        var so = sm.solar;
        if (so && so.solar_w !== null && so.solar_w !== undefined) {
            var solarSub = 'house ' + (so.house_w / 1000).toFixed(2) + ' kW, grid ' +
                (so.net_grid_w / 1000).toFixed(2) + ' kW';
            if (so.solar_kwh_24h !== undefined) {
                solarSub += ' · last 24 h ' + so.solar_kwh_24h.toFixed(0) + ' kWh; mining share ' +
                    Math.round(so.mining_share * 100) + '% = ' + money(so.mining_solar_credit_usd_day) + '/day credit';
            }
            card('Solar now', (so.solar_w / 1000).toFixed(2) + ' kW', solarSub);
            if (so.mining_solar_credit_usd_day !== null && so.mining_solar_credit_usd_day !== undefined &&
                    sm.net_loaded_usd_day !== null) {
                var after = sm.net_loaded_usd_day + so.mining_solar_credit_usd_day;
                card('Net incl. solar credit', money(after) + '/day',
                    'net incl. demand + mining share of solar', after < 0 ? 'neg' : 'pos');
            }
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
                r.stale_sources.length ? el('div', { cls: 'fleet-flag', text: 'stale: ' + r.stale_sources.join(', ') }) : null,
                r.overhead_usd_day ? el('div', { cls: 'fleet-flag',
                    text: 'with ' + r.overhead_label + ' (' + money(-r.overhead_usd_day) + '): net ' +
                        money(r.net_incl_overhead_usd_day) + '/day' }) : null,
                r.serves && r.serves.length ? el('div', { cls: 'fleet-meta', text: 'cools ' + r.serves.join(', ') }) : null
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
            } else if (a.source === 'vast') {
                lines.push('Earned last 7 days: ' + money(a.data.last_7d_usd));
                Object.keys(a.data.per_machine_7d_usd || {}).forEach(function (id) {
                    lines.push('Machine ' + id + ': ' + money(a.data.per_machine_7d_usd[id]) + ' (7 days)');
                });
                if (a.data.last_7d_usd === null || a.data.last_7d_usd === undefined) {
                    lines.push('Raw: ' + String(a.data.raw || '').slice(0, 300));
                }
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

    // ---- History ----
    var historyHours = 24;
    var PALETTE = ['#6366f1', '#22c55e', '#eab308', '#06b6d4', '#f97316', '#ec4899'];

    function label(iso, hours) {
        var d = new Date(iso);
        return hours <= 24
            ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
            : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' +
              d.toLocaleTimeString([], { hour: 'numeric' });
    }

    function lineChart(canvasId, labels, datasets, unit) {
        var canvas = document.getElementById(canvasId);
        if (!canvas || typeof Chart === 'undefined') return;
        var existing = Chart.getChart(canvas);
        if (existing) existing.destroy();
        // Dots while there are only a few snapshots (a single point draws no line)
        var dot = labels.length < 24 ? 3 : 0;
        new Chart(canvas, {
            type: 'line',
            data: { labels: labels, datasets: datasets.map(function (ds, i) {
                var color = PALETTE[i % PALETTE.length];
                return Object.assign({ borderColor: color, backgroundColor: color,
                    pointRadius: dot, borderWidth: 2, tension: 0.2, spanGaps: true }, ds);
            }) },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
                interaction: { mode: 'index', intersect: false },
                plugins: {
                    legend: { labels: { color: '#94a3b8', boxWidth: 12, font: { size: 11 } } },
                    tooltip: { callbacks: { label: function (ctx) {
                        var v = ctx.raw;
                        if (v === null || v === undefined) return ctx.dataset.label + ': —';
                        return ctx.dataset.label + ': ' + (unit === '$' ? money(v) : v.toFixed(2) + ' ' + unit);
                    } } }
                },
                scales: {
                    x: { ticks: { color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0 }, grid: { color: '#1e293b' } },
                    y: { ticks: { color: '#94a3b8', callback: function (v) { return unit === '$' ? '$' + v : v + ' ' + unit; } },
                         grid: { color: '#334155' } }
                }
            }
        });
    }

    function kwOf(w) { return w === null || w === undefined ? null : w / 1000; }

    function renderHistory(h) {
        var pts = h.points || [];
        var note = document.getElementById('fleetHistoryNote');
        if (!pts.length) {
            note.textContent = 'No history yet. The poller saves a snapshot every 5 minutes; the first appears shortly after it starts.';
            lineChart('fleetPowerChart', [], [], 'kW');
            lineChart('fleetMoneyChart', [], [], '$');
            return;
        }
        note.textContent = pts.length + ' points' + (h.bucket_s > 300 ? ', each the average of ' +
            Math.round(h.bucket_s / 60) + ' min' : ', every 5 min') + ' · since ' + label(pts[0].ts, 999);
        var labels = pts.map(function (p) { return label(p.ts, h.hours); });
        var pduNames = {};
        pts.forEach(function (p) { Object.keys(p.pdus_w || {}).forEach(function (n) { pduNames[n] = true; }); });
        var power = [
            { label: 'Sub-panel meter', data: pts.map(function (p) { return kwOf(p.meter_w); }) },
            { label: 'Known miners', data: pts.map(function (p) { return kwOf(p.miners_w); }) },
            { label: 'Solar', data: pts.map(function (p) { return kwOf(p.solar_w); }) }
        ];
        Object.keys(pduNames).sort().forEach(function (n) {
            power.push({ label: 'PDU ' + n, borderDash: [5, 4],
                data: pts.map(function (p) { return kwOf((p.pdus_w || {})[n]); }) });
        });
        lineChart('fleetPowerChart', labels, power, 'kW');
        lineChart('fleetClimateChart', labels, [
            { label: 'Hot aisle °F', data: pts.map(function (p) { return p.basement_temp_f; }) },
            { label: 'Dew point °F', data: pts.map(function (p) { return p.dew_point_f; }) },
            { label: 'Humidity %', data: pts.map(function (p) { return p.basement_humidity_pct; }) }
        ], '');
        lineChart('fleetMoneyChart', labels, [
            { label: 'Expected', data: pts.map(function (p) { return p.expected_usd_day; }) },
            { label: 'Net incl. demand', data: pts.map(function (p) { return p.net_loaded_usd_day; }) },
            { label: 'Power + demand', data: pts.map(function (p) {
                return p.power_usd_day === null || p.power_usd_day === undefined ? null
                    : p.power_usd_day + (p.demand_usd_day || 0); }) }
        ], '$');
    }

    function loadHistory() {
        fetch('/api/fleet/history?hours=' + historyHours)
            .then(function (r) { return r.json(); })
            .then(renderHistory)
            .catch(function (e) {
                document.getElementById('fleetHistoryNote').textContent = 'History failed to load: ' + e.message;
            });
    }

    document.getElementById('fleetRange').addEventListener('click', function (e) {
        var btn = e.target.closest('button[data-hours]');
        if (!btn) return;
        historyHours = parseInt(btn.getAttribute('data-hours'), 10);
        Array.prototype.forEach.call(this.querySelectorAll('button'), function (b) {
            b.classList.toggle('active', b === btn);
        });
        loadHistory();
    });

    load();
    loadHistory();
    setInterval(load, REFRESH_MS);
    setInterval(loadHistory, 5 * 60000);
})();
