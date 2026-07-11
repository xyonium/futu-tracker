// ─── Admin JavaScript ───

document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    loadUsers();
    initSnapshotSection();
});

async function apiFetch(url, opts = {}) {
    const resp = await fetch(url, opts);
    if (resp.status === 401) { window.location.href = '/login'; return null; }
    if (resp.status === 403) { alert('无管理员权限'); return null; }
    return resp.json();
}

// ─── Config ───
async function loadConfig() {
    const cfg = await apiFetch('/api/admin/config');
    if (!cfg) return;

    document.getElementById('futuHost').value = cfg.futu_host || '127.0.0.1';
    document.getElementById('futuPort').value = cfg.futu_port || '11111';
    document.getElementById('rsaKeyStatus').textContent =
        cfg.has_rsa_key ? '✅ 已配置 RSA 私钥 (留空则不更新)' : '⚠️ 未配置 RSA 私钥';
    document.getElementById('inceptionDate').value = cfg.inception_date || '';
    document.getElementById('initialCapital').value = cfg.initial_capital || '';

    // Load accounts
    const container = document.getElementById('accountsContainer');
    container.innerHTML = '';
    const accounts = cfg.accounts || [];
    if (accounts.length === 0) {
        // Add default accounts
        [
            { name: 'Futu HK', market: 'HK', acc_id: '', trd_env: 'REAL' },
            { name: 'Moomoo US', market: 'US', acc_id: '', trd_env: 'REAL' },
            { name: 'Moomoo AU', market: 'AU', acc_id: '', trd_env: 'REAL' },
        ].forEach(a => addAccountRow(a));
    } else {
        accounts.forEach(a => addAccountRow(a));
    }
}

function addAccountRow(data = {}) {
    const container = document.getElementById('accountsContainer');
    const row = document.createElement('div');
    row.className = 'account-row';
    row.innerHTML = `
        <div class="form-group" style="flex:2">
            <label>账户名称</label>
            <input type="text" class="acct-name" value="${data.name || ''}" placeholder="e.g. Futu HK">
        </div>
        <div class="form-group">
            <label>市场</label>
            <select class="acct-market">
                <option value="HK" ${data.market === 'HK' ? 'selected' : ''}>HK</option>
                <option value="US" ${data.market === 'US' ? 'selected' : ''}>US</option>
                <option value="AU" ${data.market === 'AU' ? 'selected' : ''}>AU</option>
            </select>
        </div>
        <div class="form-group">
            <label>Account ID</label>
            <input type="text" class="acct-id" value="${data.acc_id || ''}" placeholder="留空=自动">
        </div>
        <div class="form-group">
            <label>环境</label>
            <select class="acct-env">
                <option value="REAL" ${data.trd_env === 'REAL' ? 'selected' : ''}>真实</option>
                <option value="SIMULATE" ${data.trd_env === 'SIMULATE' ? 'selected' : ''}>模拟</option>
            </select>
        </div>
        <button class="btn btn-danger btn-xs" onclick="this.parentElement.remove()">删除</button>
    `;
    container.appendChild(row);
}

function collectAccounts() {
    const rows = document.querySelectorAll('.account-row');
    return Array.from(rows).map(row => ({
        name: row.querySelector('.acct-name').value.trim(),
        market: row.querySelector('.acct-market').value,
        acc_id: row.querySelector('.acct-id').value.trim(),
        trd_env: row.querySelector('.acct-env').value,
    })).filter(a => a.name);
}

async function saveAllConfig() {
    const status = document.getElementById('saveStatus');
    status.textContent = '保存中...';

    const payload = {
        futu_host: document.getElementById('futuHost').value.trim(),
        futu_port: parseInt(document.getElementById('futuPort').value) || 11111,
        inception_date: document.getElementById('inceptionDate').value,
        initial_capital: parseFloat(document.getElementById('initialCapital').value) || 1000000,
        accounts: collectAccounts(),
    };

    const rsaKey = document.getElementById('futuRsaKey').value.trim();
    if (rsaKey) payload.futu_rsa_key = rsaKey;

    const result = await apiFetch('/api/admin/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    if (result && result.ok) {
        status.textContent = '✅ 已保存';
        document.getElementById('futuRsaKey').value = '';
        loadConfig();
    } else {
        status.textContent = '❌ 保存失败';
    }
    setTimeout(() => { status.textContent = ''; }, 3000);
}

async function triggerSync() {
    const status = document.getElementById('saveStatus');
    status.textContent = '同步中...请稍候';

    const r = await apiFetch('/api/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });

    // Back-compat: the old response had only {ok, result}. The enriched
    // shape adds skipped/accounts_ok/accounts_total/errors so a total
    // fetch failure (every account threw, no daily_nav written) shows red
    // with the cause — instead of a green "HKD 0" that looks like success.
    if (r && r.ok) {
        const total = Number(r.result.total_hkd).toLocaleString();
        const okN = r.accounts_ok ?? r.result.accounts?.filter(a => a.success).length ?? '?';
        const totN = r.accounts_total ?? r.result.accounts?.length ?? '?';
        status.textContent = `✅ 同步完成: 总资产 HKD ${total}  (${okN}/${totN} 账户)`;
    } else {
        const errs = (r && Array.isArray(r.errors) && r.errors.length)
            ? r.errors.join(' | ')
            : (r && (r.error || (r.result && r.result.skipped_reason)));
        const total = r && r.accounts_total != null ? r.accounts_total : '?';
        const ok = r && r.accounts_ok != null ? r.accounts_ok : 0;
        const sk = r && r.skipped ? '（未写入当日 NAV，避免覆盖历史数据）' : '';
        status.textContent = `❌ 同步失败: ${ok}/${total} 账户成功 ${sk}${errs ? ' — ' + errs : ''}`;
    }
}

// ─── Users ───
async function loadUsers() {
    const users = await apiFetch('/api/admin/users');
    if (!users) return;

    const tbody = document.getElementById('userBody');
    tbody.innerHTML = users.map(u => {
        // Admins don't get an editable capital field — the amount they
        // see on the dashboard is the pool's initial_capital from
        // config, not a per-user override.
        const capCell = u.is_admin
            ? '<span class="text-muted">—</span>'
            : `<input type="number" step="0.01" class="cap-input" id="cap_${u.id}"
                     value="${u.personal_initial_capital ?? ''}"
                     placeholder="未设置" style="width:150px">
               <button class="btn btn-outline btn-xs"
                       onclick="saveCapital(${u.id}, '${u.username}')">保存</button>`;
        return `
        <tr>
            <td>${u.username}</td>
            <td>${u.is_admin ? '管理员' : '普通用户'}</td>
            <td>${capCell}</td>
            <td>${u.created_at || '--'}</td>
            <td>
                <button class="btn btn-outline btn-xs" onclick="changePassword(${u.id}, '${u.username}')">改密</button>
                <button class="btn btn-danger btn-xs" onclick="deleteUser(${u.id}, '${u.username}')">删除</button>
            </td>
        </tr>`;
    }).join('');
}

async function addUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const isAdmin = document.getElementById('newIsAdmin').checked;
    const capRaw = document.getElementById('newInitialCapital').value.trim();
    const personalCap = capRaw === '' ? null : parseFloat(capRaw);

    if (!username || !password) { alert('请填写用户名和密码'); return; }

    const result = await apiFetch('/api/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            username, password, is_admin: isAdmin,
            personal_initial_capital: personalCap,
        })
    });

    if (result && result.ok) {
        document.getElementById('newUsername').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('newInitialCapital').value = '';
        document.getElementById('newIsAdmin').checked = false;
        loadUsers();
    } else {
        alert(result ? result.error : '添加失败');
    }
}

async function saveCapital(id, username) {
    const raw = document.getElementById(`cap_${id}`).value.trim();
    const value = raw === '' ? null : parseFloat(raw);
    const result = await apiFetch('/api/admin/user_capital', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, personal_initial_capital: value })
    });
    if (result && result.ok) alert(`${username} 的初始资产已保存`);
    else alert(result ? result.error : '保存失败');
}

async function deleteUser(id, username) {
    if (!confirm(`确定删除用户 "${username}" ?`)) return;
    const result = await apiFetch('/api/admin/users', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id })
    });
    if (result && result.ok) loadUsers();
    else alert(result ? result.error : '删除失败');
}

async function changePassword(id, username) {
    const pwd = prompt(`为 "${username}" 设置新密码:`);
    if (!pwd) return;
    const result = await apiFetch('/api/admin/change_password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: id, new_password: pwd })
    });
    if (result && result.ok) alert('密码已修改');
    else alert(result ? result.error : '修改失败');
}

// ─── Manual historical snapshot import ───
// Rows are keyed by market. HK's rate is locked to 1.0 (it's already
// HKD). Users type native amounts; the HKD column recomputes live so
// the admin can eyeball totals before hitting save.
const SNAP_ROWS = [
    { market: 'HK', name: 'Futu HK',   currency: 'HKD', rateLocked: true  },
    { market: 'US', name: 'Moomoo US', currency: 'USD', rateLocked: false },
    { market: 'AU', name: 'Moomoo AU', currency: 'AUD', rateLocked: false },
];

function lastWeekdayOfMonth(year, monthZeroBased) {
    // JS Date: month is 0-based. Day 0 of month M+1 = last day of month M.
    const d = new Date(year, monthZeroBased + 1, 0);
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
}

function initSnapshotSection() {
    const now = new Date();
    // Default snapshot date to last weekday of the *previous* month —
    // matches the "monthly statement" workflow.
    const prevMonth = now.getMonth() === 0
        ? { y: now.getFullYear() - 1, m: 11 }
        : { y: now.getFullYear(), m: now.getMonth() - 1 };
    document.getElementById('snapDate').value = lastWeekdayOfMonth(prevMonth.y, prevMonth.m);

    const tbody = document.getElementById('snapBody');
    tbody.innerHTML = SNAP_ROWS.map(r => `
        <tr data-market="${r.market}">
            <td><strong>${r.market}</strong></td>
            <td><input type="text" class="snap-name"     value="${r.name}"     style="width:120px"></td>
            <td><input type="text" class="snap-currency" value="${r.currency}" style="width:60px"  ${r.rateLocked ? 'readonly' : ''}></td>
            <td><input type="number" step="0.01" class="snap-native" value="0" style="width:150px" oninput="recalcSnapTotal()"></td>
            <td><input type="number" step="0.000001" class="snap-rate"
                       value="${r.rateLocked ? '1.0' : ''}"
                       ${r.rateLocked ? 'readonly style="background:#222;color:#888;"' : ''}
                       style="width:110px" oninput="recalcSnapTotal()"></td>
            <td class="snap-hkd">0.00</td>
        </tr>
    `).join('');
    recalcSnapTotal();
}

function recalcSnapTotal() {
    let total = 0;
    document.querySelectorAll('#snapBody tr').forEach(tr => {
        const native = parseFloat(tr.querySelector('.snap-native').value) || 0;
        const rate = parseFloat(tr.querySelector('.snap-rate').value) || 0;
        const hkd = native * rate;
        tr.querySelector('.snap-hkd').textContent = hkd.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        total += hkd;
    });
    document.getElementById('snapTotal').textContent =
        total.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

async function fetchPbocRates() {
    const date = document.getElementById('snapDate').value;
    const status = document.getElementById('pbocStatus');
    if (!date) { alert('请先选择日期'); return; }
    status.textContent = '查询中…';

    const resp = await fetch(`/api/admin/pboc_parity?date=${date}`);
    const data = await resp.json();
    if (!resp.ok) {
        status.textContent = `❌ ${data.error || '查询失败'}`;
        return;
    }
    // Fill rates into non-locked rows
    document.querySelectorAll('#snapBody tr').forEach(tr => {
        const mkt = tr.dataset.market;
        const ccy = tr.querySelector('.snap-currency').value.trim();
        if (data.rates[ccy] != null && !tr.querySelector('.snap-rate').readOnly) {
            tr.querySelector('.snap-rate').value = data.rates[ccy];
        }
    });
    recalcSnapTotal();
    status.innerHTML =
        `✅ 已填入 <a href="${data.source_url}" target="_blank" style="color:#4a9eff">PBOC ${data.date}</a> 汇率 ` +
        `(1 HKD = ${data.pboc_raw.HKD} CNY)`;
}

async function submitSnapshot() {
    const date = document.getElementById('snapDate').value;
    const status = document.getElementById('snapStatus');
    if (!date) { alert('请先选择日期'); return; }

    const entries = Array.from(document.querySelectorAll('#snapBody tr')).map(tr => ({
        market:        tr.dataset.market,
        account_name:  tr.querySelector('.snap-name').value.trim(),
        currency:      tr.querySelector('.snap-currency').value.trim(),
        total_native:  parseFloat(tr.querySelector('.snap-native').value) || 0,
        exchange_rate: parseFloat(tr.querySelector('.snap-rate').value) || 0,
    }));

    if (entries.some(e => e.total_native > 0 && e.exchange_rate <= 0)) {
        alert('金额>0 的行必须填写汇率'); return;
    }

    status.textContent = '保存中…';
    const result = await apiFetch('/api/admin/manual_snapshot', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ date, entries })
    });
    if (result && result.ok) {
        status.textContent =
            `✅ 已保存 ${result.date}: 总资产 HKD ${result.total_hkd.toLocaleString()} ` +
            `NAV=${result.nav.toFixed(4)} (${result.pnl_pct >= 0 ? '+' : ''}${result.pnl_pct.toFixed(2)}%)`;
    } else {
        status.textContent = `❌ ${result ? result.error : '保存失败'}`;
    }
}
