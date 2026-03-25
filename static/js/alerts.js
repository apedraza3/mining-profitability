// ---- Alerts Settings UI ----

let alertConfigs = [];
let currentAlertTab = 'telegram';

function openAlertsModal() {
    document.getElementById('alertsModal').style.display = 'flex';
    loadAlertConfig();
}

function closeAlertsModal() {
    document.getElementById('alertsModal').style.display = 'none';
}

function switchAlertTab(tab) {
    currentAlertTab = tab;
    // Update tab styles
    ['Telegram', 'Discord', 'Log'].forEach(name => {
        const btn = document.getElementById('alertTab' + name);
        const key = name.toLowerCase();
        if (key === tab) {
            btn.style.borderBottomColor = 'var(--primary)';
            btn.style.color = 'var(--text)';
        } else {
            btn.style.borderBottomColor = 'transparent';
            btn.style.color = 'var(--text-muted)';
        }
    });
    // Show/hide panels
    document.getElementById('alertPanelTelegram').style.display = tab === 'telegram' ? 'block' : 'none';
    document.getElementById('alertPanelDiscord').style.display = tab === 'discord' ? 'block' : 'none';
    document.getElementById('alertPanelLog').style.display = tab === 'log' ? 'block' : 'none';
    document.getElementById('alertTypesPanel').style.display = tab === 'log' ? 'none' : 'block';
    document.getElementById('alertActions').style.display = tab === 'log' ? 'none' : 'flex';

    if (tab === 'log') {
        loadRecentAlerts();
    }
}

async function loadAlertConfig() {
    try {
        const resp = await fetch('/api/settings/alerts');
        if (!resp.ok) throw new Error('Failed to load alert config');
        alertConfigs = await resp.json();

        // Populate Telegram fields
        const tgConfig = alertConfigs.find(c => c.channel === 'telegram');
        if (tgConfig) {
            document.getElementById('alertTgBotToken').value = tgConfig.bot_token || '';
            document.getElementById('alertTgChatId').value = tgConfig.chat_id || '';
            document.getElementById('alertTgEnabled').checked = !!tgConfig.enabled;
            document.getElementById('alertOptOffline').checked = !!tgConfig.alert_offline;
            document.getElementById('alertOptHashrate').checked = !!tgConfig.alert_hashrate_drop;
            document.getElementById('alertHashratePct').value = tgConfig.hashrate_drop_pct || 20;
            document.getElementById('alertOptNegProfit').checked = !!tgConfig.alert_negative_profit;
            document.getElementById('alertOptDailySummary').checked = !!tgConfig.alert_daily_summary;
        }

        // Populate Discord fields
        const dcConfig = alertConfigs.find(c => c.channel === 'discord');
        if (dcConfig) {
            document.getElementById('alertDiscordWebhook').value = dcConfig.webhook_url || '';
            document.getElementById('alertDiscordEnabled').checked = !!dcConfig.enabled;
            // If no Telegram config, use Discord settings for the shared options
            if (!tgConfig) {
                document.getElementById('alertOptOffline').checked = !!dcConfig.alert_offline;
                document.getElementById('alertOptHashrate').checked = !!dcConfig.alert_hashrate_drop;
                document.getElementById('alertHashratePct').value = dcConfig.hashrate_drop_pct || 20;
                document.getElementById('alertOptNegProfit').checked = !!dcConfig.alert_negative_profit;
                document.getElementById('alertOptDailySummary').checked = !!dcConfig.alert_daily_summary;
            }
        }
    } catch (err) {
        console.error('Load alert config error:', err);
    }
}

async function saveAlertConfig() {
    const sharedSettings = {
        alert_offline: document.getElementById('alertOptOffline').checked,
        alert_hashrate_drop: document.getElementById('alertOptHashrate').checked,
        hashrate_drop_pct: parseFloat(document.getElementById('alertHashratePct').value) || 20,
        alert_negative_profit: document.getElementById('alertOptNegProfit').checked,
        alert_daily_summary: document.getElementById('alertOptDailySummary').checked,
    };

    const saves = [];

    // Save Telegram config
    const tgToken = document.getElementById('alertTgBotToken').value.trim();
    const tgChatId = document.getElementById('alertTgChatId').value.trim();
    if (tgToken || tgChatId) {
        saves.push(fetch('/api/settings/alerts', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel: 'telegram',
                bot_token: tgToken,
                chat_id: tgChatId,
                enabled: document.getElementById('alertTgEnabled').checked,
                ...sharedSettings,
            }),
        }));
    }

    // Save Discord config
    const dcWebhook = document.getElementById('alertDiscordWebhook').value.trim();
    if (dcWebhook) {
        saves.push(fetch('/api/settings/alerts', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                channel: 'discord',
                webhook_url: dcWebhook,
                enabled: document.getElementById('alertDiscordEnabled').checked,
                ...sharedSettings,
            }),
        }));
    }

    if (saves.length === 0) {
        showToast('Enter at least one Telegram or Discord configuration', 'warning');
        return;
    }

    try {
        const results = await Promise.all(saves);
        const allOk = results.every(r => r.ok);
        if (allOk) {
            showToast('Alert settings saved', 'success');
        } else {
            showToast('Some alert settings failed to save', 'error');
        }
    } catch (err) {
        showToast('Error saving alert settings', 'error');
        console.error('Save alert config error:', err);
    }
}

async function testAlert() {
    try {
        const resp = await fetch('/api/settings/alerts/test', { method: 'POST' });
        const data = await resp.json();
        if (data.success) {
            showToast('Test alert sent! Check your channels.', 'success');
        } else {
            showToast(data.message || 'Test alert failed', 'error');
        }
    } catch (err) {
        showToast('Error sending test alert', 'error');
        console.error('Test alert error:', err);
    }
}

async function loadRecentAlerts() {
    const container = document.getElementById('alertLogList');
    try {
        const resp = await fetch('/api/alerts/recent?limit=50');
        if (!resp.ok) throw new Error('Failed to load alerts');
        const alerts = await resp.json();

        if (!alerts.length) {
            container.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;padding:12px 0;">No alerts sent yet.</p>';
            return;
        }

        let html = '<table style="width:100%;border-collapse:collapse;font-size:0.8rem;">';
        html += '<thead><tr style="border-bottom:1px solid var(--border);">';
        html += '<th style="text-align:left;padding:6px 8px;color:var(--text-muted);">Time</th>';
        html += '<th style="text-align:left;padding:6px 8px;color:var(--text-muted);">Type</th>';
        html += '<th style="text-align:left;padding:6px 8px;color:var(--text-muted);">Channel</th>';
        html += '<th style="text-align:left;padding:6px 8px;color:var(--text-muted);">Message</th>';
        html += '</tr></thead><tbody>';

        for (const a of alerts) {
            const typeColors = {
                offline: 'var(--danger)',
                hashrate_drop: 'var(--warning)',
                negative_profit: 'var(--danger)',
                daily_summary: 'var(--primary)',
                test: 'var(--success)',
            };
            const color = typeColors[a.alert_type] || 'var(--text-muted)';
            const time = new Date(a.sent_at).toLocaleString();
            // Strip HTML tags from message for table display
            const plainMsg = a.message.replace(/<[^>]+>/g, '').substring(0, 80);
            html += `<tr style="border-bottom:1px solid var(--border);">`;
            html += `<td style="padding:6px 8px;color:var(--text-muted);white-space:nowrap;">${time}</td>`;
            html += `<td style="padding:6px 8px;"><span style="color:${color};font-weight:500;">${a.alert_type}</span></td>`;
            html += `<td style="padding:6px 8px;color:var(--text-muted);">${a.channel}</td>`;
            html += `<td style="padding:6px 8px;color:var(--text);">${plainMsg}</td>`;
            html += `</tr>`;
        }

        html += '</tbody></table>';
        container.innerHTML = html;
    } catch (err) {
        container.innerHTML = '<p style="color:var(--danger);font-size:0.85rem;">Failed to load alert log.</p>';
        console.error('Load recent alerts error:', err);
    }
}

// Close modal on overlay click
document.addEventListener('click', (e) => {
    if (e.target.id === 'alertsModal') {
        closeAlertsModal();
    }
});
