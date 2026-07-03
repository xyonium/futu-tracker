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
    // Per-user assets overview — also admin-only, also DOM-gated.
    if (document.getElementById('userAssetsBody')) {
        loadUserAssets();
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

    const ctx = document.getElementById('navChart').getContext('2d');
    if (navChart) navChart.destroy();

    if (data.is_admin) {
        navChart = buildAdminStackChart(ctx, data.data);
    } else {
        navChart = buildNavCurveChart(ctx, data.data);
    }
}

// ─── Regular-user chart: pure NAV curve (unchanged behavior) ───
function buildNavCurveChart(ctx, series) {
    const labels    = series.map(d => d.date);
    const navValues = series.map(d => d.nav);
    const pnlValues = series.map(d => d.pnl_pct);

    const lastPnl = pnlValues[pnlValues.length - 1];
    const lineColor = lastPnl >= 0 ? '#22c55e' : '#ef4444';
    const fillColor = lastPnl >= 0 ? 'rgba(34,197,94,0.08)' : 'rgba(239,68,68,0.08)';

    return new Chart(ctx, {
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
            interaction: { intersect: false, mode: 'index' },
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

// ─── Admin chart: stacked HKD by market ───
// HK/US/AU are stacked; the visual top of the stack == total_assets_hkd
// on daily_nav for that date. Tooltip shows the total (HKD) plus each
// market's native balance (原币).
const MARKET_ORDER = ['HK', 'US', 'AU'];
const MARKET_META = {
    HK: { label: '香港 (HK)',   color: '#22c55e', fill: 'rgba(34,197,94,0.35)'  },
    US: { label: '美国 (US)',   color: '#3b82f6', fill: 'rgba(59,130,246,0.35)' },
    AU: { label: '澳洲 (AU)',   color: '#f59e0b', fill: 'rgba(245,158,11,0.35)' },
};

function buildAdminStackChart(ctx, series) {
    const labels = series.map(d => d.date);
    const totals = series.map(d => d.total_assets_hkd);

    // Native balance is what we show in the tooltip. Stash it alongside
    // the HKD-series values so the tooltip callback can pull it out
    // without a second lookup — Chart.js only exposes the raw dataset
    // and dataIndex there.
    const datasets = MARKET_ORDER.map(mkt => {
        const meta = MARKET_META[mkt];
        return {
            label: meta.label,
            data: series.map(d => (d.markets && d.markets[mkt]) ? d.markets[mkt].hkd : 0),
            _native: series.map(d => (d.markets && d.markets[mkt]) ? d.markets[mkt].native : 0),
            _currency: series.map(d => (d.markets && d.markets[mkt]) ? d.markets[mkt].currency : ''),
            borderColor: meta.color,
            backgroundColor: meta.fill,
            borderWidth: 1.5,
            fill: true,
            tension: 0.25,
            pointRadius: labels.length > 60 ? 0 : 2,
            pointHoverRadius: 4,
            stack: 'assets',
        };
    });

    const fmtHKD    = v => Number(v).toLocaleString('en-US', {maximumFractionDigits: 0});
    const fmtNative = v => Number(v).toLocaleString('en-US', {maximumFractionDigits: 2});

    return new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    display: true,
                    position: 'top',
                    labels: { color: '#c8ccdc', boxWidth: 12 }
                },
                tooltip: {
                    callbacks: {
                        title: (items) => items[0].label,
                        // First row of the tooltip body: the daily total.
                        // We emit it as a synthetic line before the market
                        // rows so it always shows first.
                        beforeBody: (items) => {
                            const idx = items[0].dataIndex;
                            return `总额 HKD: ${fmtHKD(totals[idx])}`;
                        },
                        label: (item) => {
                            const idx = item.dataIndex;
                            const ds = item.dataset;
                            const native = ds._native[idx];
                            const ccy = ds._currency[idx] || '';
                            if (!native) return `${ds.label}: —`;
                            return `${ds.label}: ${fmtNative(native)} ${ccy}`;
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
                    stacked: true,
                    grid: { color: 'rgba(255,255,255,0.05)' },
                    ticks: {
                        color: '#8b8fa3',
                        callback: (v) => {
                            // Compact HKD axis: 1.2M / 850K etc.
                            const abs = Math.abs(v);
                            if (abs >= 1e6) return (v / 1e6).toFixed(1) + 'M';
                            if (abs >= 1e3) return (v / 1e3).toFixed(0) + 'K';
                            return v.toFixed(0);
                        }
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

// ─── Per-user assets overview (admin dashboard table) ───
// Pool row + one row per non-admin user. Null initial_capital → '--'
// (user hasn't been given an anchor yet), matching /api/summary's
// degrade behavior.
async function loadUserAssets() {
    const data = await apiFetch('/api/admin/user_assets');
    if (!data || !data.has_data) return;

    document.getElementById('userAssetsDate').textContent = `(${data.date})`;

    const fmtMoney = v => formatNumber(v, 0);
    const pnlCell = (pnl) => {
        if (pnl == null) return '<td>--</td>';
        const cls = pnl >= 0 ? 'positive' : 'negative';
        const sign = pnl >= 0 ? '+' : '';
        return `<td class="${cls}">${sign}${fmtMoney(pnl)}</td>`;
    };

    const pool = data.pool;
    let html = `
        <tr style="font-weight:700; border-top:2px solid var(--border)">
            <td>整体 (资金池)</td>
            <td>${fmtMoney(pool.initial)}</td>
            <td>${fmtMoney(pool.current)}</td>
            ${pnlCell(pool.pnl)}
        </tr>`;

    html += (data.users || []).map(u => {
        const init = u.initial == null ? '--' : fmtMoney(u.initial);
        const cur  = u.current == null ? '--' : fmtMoney(u.current);
        return `
        <tr>
            <td>${u.username}</td>
            <td>${init}</td>
            <td>${cur}</td>
            ${pnlCell(u.pnl)}
        </tr>`;
    }).join('');

    document.getElementById('userAssetsBody').innerHTML = html;
}
