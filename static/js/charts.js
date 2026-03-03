// ---- ROI Chart ----
function renderROIChart(roi) {
    const canvas = document.getElementById('roiChart');
    if (!canvas) return;

    // Destroy existing chart if any
    const existing = Chart.getChart(canvas);
    if (existing) existing.destroy();

    const investment = roi.total_investment;
    const dailyProfit = roi.best_daily_profit * (roi.total_investment > 0 ? 1 : 0);
    const daysToShow = Math.min(Math.max(roi.days_to_roi * 1.3, 90), 730); // Show up to 130% of ROI or min 90 days

    const labels = [];
    const cumulativeProfit = [];
    const investmentLine = [];

    for (let day = 0; day <= daysToShow; day++) {
        labels.push(day);
        cumulativeProfit.push(dailyProfit * day);
        investmentLine.push(investment);
    }

    new Chart(canvas, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Cumulative Profit',
                    data: cumulativeProfit,
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: 'Investment',
                    data: investmentLine,
                    borderColor: '#ef4444',
                    borderDash: [6, 4],
                    pointRadius: 0,
                    borderWidth: 2,
                    fill: false,
                },
            ],
        },
        options: {
            responsive: true,
            plugins: {
                legend: {
                    labels: { color: '#94a3b8', font: { size: 12 } },
                },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            return ctx.dataset.label + ': $' + ctx.raw.toFixed(2);
                        },
                        title: items => 'Day ' + items[0].label,
                    },
                },
            },
            scales: {
                x: {
                    title: { display: true, text: 'Days', color: '#94a3b8' },
                    ticks: { color: '#64748b', maxTicksLimit: 12 },
                    grid: { color: 'rgba(51, 65, 85, 0.5)' },
                },
                y: {
                    title: { display: true, text: 'USD ($)', color: '#94a3b8' },
                    ticks: {
                        color: '#64748b',
                        callback: val => '$' + val.toLocaleString(),
                    },
                    grid: { color: 'rgba(51, 65, 85, 0.5)' },
                },
            },
            interaction: { intersect: false, mode: 'index' },
        },
    });
}
