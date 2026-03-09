// ---- Miner Modal ----
function openMinerModal(minerId) {
    const modal = document.getElementById('minerModal');
    const title = document.getElementById('minerModalTitle');
    const form = document.getElementById('minerForm');
    form.reset();
    document.getElementById('minerFormId').value = '';

    if (minerId) {
        title.textContent = 'Edit Miner';
        const miner = dashboardData?.miners?.find(r => r.miner.id === minerId)?.miner;
        if (miner) {
            document.getElementById('minerFormId').value = miner.id;
            document.getElementById('minerName').value = miner.name || '';
            document.getElementById('minerModel').value = miner.model || '';
            document.getElementById('minerType').value = miner.type || 'ASIC';
            document.getElementById('minerAlgorithm').value = miner.algorithm || '';
            document.getElementById('minerHashrate').value = miner.hashrate || '';
            document.getElementById('minerHashrateUnit').value = miner.hashrate_unit || 'TH/s';
            document.getElementById('minerWattage').value = miner.wattage || '';
            document.getElementById('minerLocation').value = miner.location_id || '';
            document.getElementById('minerQuantity').value = miner.quantity || 1;
            document.getElementById('minerPurchasePrice').value = miner.purchase_price || '';
            document.getElementById('minerPurchaseDate').value = miner.purchase_date || '';
            document.getElementById('minerStatus').value = miner.status || 'active';
            document.getElementById('minerHrnKey').value = miner.hashrateno_model_key || '';
            document.getElementById('minerMnKey').value = miner.miningnow_model_key || '';
            document.getElementById('minerPpKey').value = miner.powerpool_worker_key || '';
        }
    } else {
        title.textContent = 'Add Miner';
    }

    modal.style.display = 'flex';
}

function closeMinerModal() {
    document.getElementById('minerModal').style.display = 'none';
}

function editMiner(id) {
    openMinerModal(id);
}

async function saveMiner(event) {
    event.preventDefault();

    const id = document.getElementById('minerFormId').value;
    const data = {
        name: document.getElementById('minerName').value.trim(),
        model: document.getElementById('minerModel').value.trim(),
        type: document.getElementById('minerType').value,
        algorithm: document.getElementById('minerAlgorithm').value,
        hashrate: parseFloat(document.getElementById('minerHashrate').value),
        hashrate_unit: document.getElementById('minerHashrateUnit').value,
        wattage: parseInt(document.getElementById('minerWattage').value),
        location_id: document.getElementById('minerLocation').value,
        quantity: parseInt(document.getElementById('minerQuantity').value) || 1,
        purchase_price: parseFloat(document.getElementById('minerPurchasePrice').value) || 0,
        purchase_date: document.getElementById('minerPurchaseDate').value || '',
        status: document.getElementById('minerStatus').value,
        hashrateno_model_key: document.getElementById('minerHrnKey').value.trim(),
        miningnow_model_key: document.getElementById('minerMnKey').value.trim(),
        powerpool_worker_key: document.getElementById('minerPpKey').value.trim(),
    };

    try {
        let resp;
        if (id) {
            resp = await fetch(`/api/miners/${id}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
        } else {
            resp = await fetch('/api/miners', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
        }
        if (!resp.ok) throw new Error('Save failed');
        closeMinerModal();
        showToast(id ? 'Miner updated' : 'Miner added', 'success');
        loadDashboard();
    } catch (err) {
        showToast('Failed to save miner: ' + err.message, 'error');
    }
}

// ---- Locations Modal ----
function openLocationsModal() {
    const modal = document.getElementById('locationsModal');
    renderLocationsList();
    modal.style.display = 'flex';
}

function closeLocationsModal() {
    document.getElementById('locationsModal').style.display = 'none';
}

function renderLocationsList() {
    const list = document.getElementById('locationsList');
    list.innerHTML = '';
    locations.forEach(loc => {
        const row = document.createElement('div');
        row.className = 'location-row';
        row.innerHTML = `
            <input type="text" value="${esc(loc.name)}" data-id="${loc.id}" data-field="name" onchange="updateLocationField(this)" placeholder="Name">
            <input type="number" value="${loc.electricity_cost_kwh}" step="0.01" min="0" max="1" data-id="${loc.id}" data-field="electricity_cost_kwh" onchange="updateLocationField(this)" title="Electricity $/kWh">
            <input type="number" value="${loc.solar_daily_kwh || 0}" step="0.1" min="0" data-id="${loc.id}" data-field="solar_daily_kwh" onchange="updateLocationField(this)" title="Avg daily solar production (kWh)" placeholder="Solar kWh/day">
            <button class="btn-sm delete" onclick="deleteLocation('${loc.id}')" title="Delete">&times;</button>
        `;
        list.appendChild(row);
    });
}

async function addLocationRow() {
    try {
        const resp = await fetch('/api/locations', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: 'New Location', electricity_cost_kwh: 0.10 }),
        });
        if (!resp.ok) throw new Error('Failed to add location');
        await loadLocations();
        renderLocationsList();
        showToast('Location added', 'success');
    } catch (err) {
        showToast('Failed to add location', 'error');
    }
}

async function updateLocationField(input) {
    const id = input.dataset.id;
    const field = input.dataset.field;
    let value = input.value;
    if (field === 'electricity_cost_kwh' || field === 'solar_daily_kwh') value = parseFloat(value) || 0;

    try {
        await fetch(`/api/locations/${id}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ [field]: value }),
        });
        await loadLocations();
    } catch (err) {
        showToast('Failed to update location', 'error');
    }
}

async function deleteLocation(id) {
    if (!confirm('Delete this location? Miners assigned to it will show as "Unknown" location.')) return;
    try {
        await fetch(`/api/locations/${id}`, { method: 'DELETE' });
        await loadLocations();
        renderLocationsList();
        showToast('Location deleted', 'success');
    } catch (err) {
        showToast('Failed to delete location', 'error');
    }
}
