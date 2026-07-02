// ─── Dashboard JavaScript ───
let navChart = null;
let currentPeriod = 'all';

document.addEventListener('DOMContentLoaded', () => {
    loadSummary();
    loadChart('all');
    // Account breakdown table is admin-only; the template omits its DOM
    // for non-admin users so we skip the fetch entirely there.
    if (document.getElementById('accountBody')) {
        loadAccountDetail();
    }

    document.querySelectorAll('.period-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentPeriod = btn.dataset.period;
            loadChart(currentPeriod);
        });
    });
});

async function apiFetch(url) {
    const resp = await fetch(url);
    if (resp.status === 401) {
        window.location.href = '/login';
        return null;
    }
    return resp.json();
}

function formatNumber(n, decimals = 2) {
    if (n == null) return '--';
    return Number(n).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

async function loadSummary() {
    const data = await apiFetch('/api/summary');
    if (!data || !data.has_data) return;

    document.getElementById('totalAssets').textContent = formatNumber(data.total_assets_hkd, 0);

    const navEl = document.getElementById('currentNav');
    navEl.textContent = formatNumber(data.nav, 4);

    const pctEl = document.getElementById('pnlPct');
    const pctVal = data.pnl_pct;
    pctEl.textContent = (pctVal >= 0 ? '+' : '') + formatNumber(pctVal) + '%';
    pctEl.className = 'card-value ' + (pctVal >= 0 ? 'positive' : 'negative');

    const amtEl = document.getElementById('pnlAmount');
    const amtVal = data.pnl_amount;
    if (amtVal == null) {
        // Non-admin user without a personal_initial_capital set — leave
        // the amount card blank rather than showing a misleading '+0'.
        amtEl.textContent = '--';
        amtEl.className = 'card-value';
    } else {
        amtEl.textContent = (amtVal >= 0 ? '+' : '') + formatNumber(amtVal, 0);
        amtEl.className = 'card-value ' + (amtVal >= 0 ? 'positive' : 'negative');
    }
}

async function loadChart(period) {
    const data = await apiFetch(`/api/nav_history?period=${period}`);
    if (!data || !data.data || data.data.length === 0) return;

    const labels = data.data.map(d => d.date);
    const navValues = data.data.map(d => d.nav);
    const pnlValues = data.data.map(d => d.pnl_pct);

    const ctx = document.getElementById('navChart').getContext('2d');

    if (navChart) navChart.destroy();

    // Determine chart color based on latest PnL
    const lastPnl = pnlValues[pnlValues.length - 1];
    const lineColor = lastPnl >= 0 ? '#22c55e' : '#ef4444';
    const fillColor = lastPnl >= 0 ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

    navChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: '净值',
                data: navValues,
                borderColor: lineColor,
                backgroundColor: fillColor,
                borderWidth: 2,
                fill: true,
                tension: 0.3,
                pointRadius: labels.length > 60 ? 0 : 3,
                pointHoverRadius: 5,
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        title: (items) => items[0].label,
                        label: (item) => {
                            const idx = item.dataIndex;
                            const nav = navValues[idx].toFixed(4);
                            const pnl = pnlValues[idx].toFixed(2);
                            const sign = pnlValues[idx] >= 0 ? '+' : '';
                            return [`净值: ${nav}`, `盈亏: ${sign}${pnl}%`];
                        }
                    }
                }
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: labels.length > 180 ? 'month' : (labels.length > 30 ? 'week' : 'day'),
                        tooltipFormat: 'yyyy-MM-dd'
                    },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: { color: '#8b8fa3', maxTicksLimit: 12 }
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: '#8b8fa3',
                        callback: (v) => v.toFixed(3)
                    }
                }
            }
        }
    });
}

async function loadAccountDetail() {
    const data = await apiFetch('/api/account_detail');
    if (!data || data.length === 0) return;

    document.getElementById('detailDate').textContent = `(${data[0].date})`;

    const tbody = document.getElementById('accountBody');
    let totalHkd = 0;
    let html = '';

    data.forEach(row => {
        totalHkd += row.total_assets_hkd;
        html += `<tr>
            <td>${row.account_name}</td>
            <td>${row.currency}</td>
            <td>${formatNumber(row.total_assets, 2)}</td>
            <td>${formatNumber(row.exchange_rate, 4)}</td>
            <td>${formatNumber(row.total_assets_hkd, 0)}</td>
        </tr>`;
    });

    html += `<tr style="font-weight:700; border-top:2px solid var(--border)">
        <td colspan="4">合计</td>
        <td>${formatNumber(totalHkd, 0)}</td>
    </tr>`;

    tbody.innerHTML = html;
}
