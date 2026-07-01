// ─── Admin JavaScript ───

document.addEventListener('DOMContentLoaded', () => {
    loadConfig();
    loadUsers();
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

    const result = await apiFetch('/api/sync', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
    });

    if (result && result.ok) {
        status.textContent = `✅ 同步完成: 总资产 HKD ${Number(result.result.total_hkd).toLocaleString()}`;
    } else {
        status.textContent = `❌ 同步失败: ${result ? result.error : 'Unknown error'}`;
    }
}

// ─── Users ───
async function loadUsers() {
    const users = await apiFetch('/api/admin/users');
    if (!users) return;

    const tbody = document.getElementById('userBody');
    tbody.innerHTML = users.map(u => `
        <tr>
            <td>${u.username}</td>
            <td>${u.is_admin ? '管理员' : '普通用户'}</td>
            <td>${u.created_at || '--'}</td>
            <td>
                <button class="btn btn-outline btn-xs" onclick="changePassword(${u.id}, '${u.username}')">改密</button>
                <button class="btn btn-danger btn-xs" onclick="deleteUser(${u.id}, '${u.username}')">删除</button>
            </td>
        </tr>
    `).join('');
}

async function addUser() {
    const username = document.getElementById('newUsername').value.trim();
    const password = document.getElementById('newPassword').value;
    const isAdmin = document.getElementById('newIsAdmin').checked;

    if (!username || !password) { alert('请填写用户名和密码'); return; }

    const result = await apiFetch('/api/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, is_admin: isAdmin })
    });

    if (result && result.ok) {
        document.getElementById('newUsername').value = '';
        document.getElementById('newPassword').value = '';
        document.getElementById('newIsAdmin').checked = false;
        loadUsers();
    } else {
        alert(result ? result.error : '添加失败');
    }
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
