#!/usr/bin/env python3
"""
Futu Portfolio Tracker - Main Application
Multi-account portfolio tracking with currency conversion and NAV calculation.
"""

import os
import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from functools import wraps
from contextlib import contextmanager

from flask import (Flask, render_template, request, jsonify, session,
                   redirect, url_for, flash)
from werkzeug.security import generate_password_hash, check_password_hash
from cryptography.fernet import Fernet

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=4)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'portfolio.db')
ENCRYPTION_KEY_FILE = os.path.join(os.path.dirname(__file__), 'data', '.encryption_key')


# ─────────────────────────────────────────────
# Encryption for API keys
# ─────────────────────────────────────────────
def get_encryption_key():
    """Get or create Fernet encryption key for API key storage."""
    os.makedirs(os.path.dirname(ENCRYPTION_KEY_FILE), exist_ok=True)
    if os.path.exists(ENCRYPTION_KEY_FILE):
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    key = Fernet.generate_key()
    with open(ENCRYPTION_KEY_FILE, 'wb') as f:
        f.write(key)
    os.chmod(ENCRYPTION_KEY_FILE, 0o600)
    return key


def encrypt_value(value: str) -> str:
    f = Fernet(get_encryption_key())
    return f.encrypt(value.encode()).decode()


def decrypt_value(encrypted: str) -> str:
    f = Fernet(get_encryption_key())
    return f.decrypt(encrypted.encode()).decode()


# ─────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────
@contextmanager
def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                is_admin INTEGER DEFAULT 0,
                -- Per-user "how much did I actually invest" anchor. Non-admin
                -- users see total_assets_hkd and pnl_amount rebased against
                -- this value (their share of the pool), while the global
                -- initial_capital in `config` continues to drive the nav
                -- curve. NULL means "not set" — the user's numeric display
                -- degrades to '--' until an admin fills it in.
                personal_initial_capital REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                account_name TEXT NOT NULL,
                market TEXT NOT NULL DEFAULT '',
                currency TEXT NOT NULL,
                total_assets REAL NOT NULL,
                total_assets_hkd REAL NOT NULL,
                exchange_rate REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date, account_name)
            );
            CREATE TABLE IF NOT EXISTS daily_nav (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT UNIQUE NOT NULL,
                total_assets_hkd REAL NOT NULL,
                nav REAL NOT NULL,
                pnl_pct REAL NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        # Backfill column on pre-existing databases. CREATE TABLE IF NOT
        # EXISTS above is a no-op when the table already exists, so we
        # ALTER TABLE separately. SQLite has no "ADD COLUMN IF NOT EXISTS"
        # — probe pragma_table_info instead.
        cols = [r['name'] for r in conn.execute(
            "SELECT name FROM pragma_table_info('users')"
        ).fetchall()]
        if 'personal_initial_capital' not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN personal_initial_capital REAL"
            )

        # Create default admin if not exists
        admin = conn.execute("SELECT id FROM users WHERE username='admin'").fetchone()
        if not admin:
            conn.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                ('admin', generate_password_hash('admin123'))
            )
            print("[INIT] Default admin created: admin / admin123  *** CHANGE THIS ***")


# ─────────────────────────────────────────────
# Auth decorators
# ─────────────────────────────────────────────
def _wants_json():
    """True if the current request is an API call rather than a browser
    navigation. `request.is_json` only catches requests with a JSON
    *body*, missing GETs that expect JSON back. Paths under /api/ are
    always API endpoints, so we treat them as JSON regardless."""
    return request.is_json or request.path.startswith('/api/')


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if _wants_json():
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if _wants_json():
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            if _wants_json():
                return jsonify({'error': 'Forbidden'}), 403
            flash('Admin access required', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
# Config helpers
# ─────────────────────────────────────────────
def get_config(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default


def set_config(key, value):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO config (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, datetime.now().isoformat())
        )


# ─────────────────────────────────────────────
# Routes: Auth
# ─────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    data = request.form if not request.is_json else request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')

    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE username=?", (username,)
        ).fetchone()

    if user and check_password_hash(user['password_hash'], password):
        session.permanent = True
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['is_admin'] = bool(user['is_admin'])
        if request.is_json:
            return jsonify({'ok': True, 'is_admin': bool(user['is_admin'])})
        return redirect(url_for('dashboard'))

    if request.is_json:
        return jsonify({'error': 'Invalid credentials'}), 401
    flash('Invalid username or password', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# ─────────────────────────────────────────────
# Routes: Dashboard
# ─────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    return render_template('dashboard.html',
                           username=session.get('username'),
                           is_admin=session.get('is_admin'))


# ─────────────────────────────────────────────
# Routes: API - Portfolio Data
# ─────────────────────────────────────────────
@app.route('/api/nav_history')
@login_required
def api_nav_history():
    """Return NAV history for charting. Supports period filtering."""
    period = request.args.get('period', 'all')  # 3m, 6m, 12m, ytd, all
    inception_date = get_config('inception_date', '2024-01-01')

    today = datetime.now().date()
    if period == '3m':
        start = (today - timedelta(days=90)).isoformat()
    elif period == '6m':
        start = (today - timedelta(days=180)).isoformat()
    elif period == '12m':
        start = (today - timedelta(days=365)).isoformat()
    elif period == 'ytd':
        start = f'{today.year}-01-01'
    else:
        start = inception_date

    # Ensure we never go before inception
    if start < inception_date:
        start = inception_date

    is_admin = bool(session.get('is_admin'))

    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, total_assets_hkd, nav, pnl_pct FROM daily_nav "
            "WHERE date >= ? ORDER BY date",
            (start,)
        ).fetchall()

        # Admin chart wants a stacked breakdown by market — pull all
        # account_snapshots in the range in one query and pivot into
        # {date: {market: {hkd, native, currency}}} for the response.
        # Regular users don't see raw HKD amounts, so we skip this.
        by_date_market = {}
        if is_admin and rows:
            snap_rows = conn.execute(
                "SELECT date, market, currency, total_assets, total_assets_hkd "
                "FROM account_snapshots WHERE date >= ? ORDER BY date",
                (start,)
            ).fetchall()
            for r in snap_rows:
                mkt = (r['market'] or '').upper()
                if not mkt:
                    continue
                by_date_market.setdefault(r['date'], {})[mkt] = {
                    'hkd': r['total_assets_hkd'],
                    'native': r['total_assets'],
                    'currency': r['currency'],
                }

    data = []
    for r in rows:
        entry = dict(r)
        if is_admin:
            entry['markets'] = by_date_market.get(r['date'], {})
        data.append(entry)

    return jsonify({
        'inception_date': inception_date,
        'initial_capital': float(get_config('initial_capital', '1000000')),
        'is_admin': is_admin,
        'data': data,
    })


@app.route('/api/account_detail')
@login_required
def api_account_detail():
    """Return per-account breakdown for a specific date or latest."""
    date = request.args.get('date')
    with get_db() as conn:
        if date:
            rows = conn.execute(
                "SELECT * FROM account_snapshots WHERE date=? ORDER BY account_name",
                (date,)
            ).fetchall()
        else:
            # Latest date
            latest = conn.execute(
                "SELECT MAX(date) as d FROM account_snapshots"
            ).fetchone()
            if latest and latest['d']:
                rows = conn.execute(
                    "SELECT * FROM account_snapshots WHERE date=? ORDER BY account_name",
                    (latest['d'],)
                ).fetchall()
            else:
                rows = []
    return jsonify([dict(r) for r in rows])


@app.route('/api/summary')
@login_required
def api_summary():
    """Current portfolio summary.

    Admins see the raw pool figures (initial_capital from config,
    total_assets_hkd summed across all accounts).

    Non-admins see the same *ratio* (nav, pnl_pct — the pool is one
    portfolio so its returns are a property of the pool, not the user)
    but total_assets_hkd and pnl_amount are rebased against their
    personal_initial_capital: what a personal_initial of X would be
    worth today given the pool's current nav.

    If personal_initial_capital is NULL, we return the amount fields as
    None so the dashboard renders '--' rather than a misleading zero.
    """
    with get_db() as conn:
        latest = conn.execute(
            "SELECT * FROM daily_nav ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not latest:
        return jsonify({'has_data': False})

    is_admin = bool(session.get('is_admin'))
    nav = latest['nav']
    pnl_pct = latest['pnl_pct']

    if is_admin:
        initial_capital = float(get_config('initial_capital', '1000000'))
        total_assets_hkd = latest['total_assets_hkd']
        pnl_amount = total_assets_hkd - initial_capital
    else:
        with get_db() as conn:
            row = conn.execute(
                "SELECT personal_initial_capital FROM users WHERE id=?",
                (session.get('user_id'),)
            ).fetchone()
        personal = row['personal_initial_capital'] if row else None
        if personal is None:
            initial_capital = None
            total_assets_hkd = None
            pnl_amount = None
        else:
            initial_capital = float(personal)
            total_assets_hkd = initial_capital * nav
            pnl_amount = total_assets_hkd - initial_capital

    return jsonify({
        'has_data': True,
        'date': latest['date'],
        'total_assets_hkd': total_assets_hkd,
        'nav': nav,
        'pnl_pct': pnl_pct,
        'initial_capital': initial_capital,
        'pnl_amount': pnl_amount,
        'is_admin': is_admin,
    })


@app.route('/api/admin/user_assets')
@admin_required
def api_admin_user_assets():
    """Admin-only: per-user initial / current / pnl overview for the
    dashboard table.

    Same rebase logic as /api/summary — a regular user's "current
    assets" is `personal_initial_capital * nav` (their share of the
    pool given the pool's current nav), and pnl is that minus their
    initial. Admins themselves are excluded from the per-user list:
    the pool row already represents them, so listing the admin account
    would double-count. Users with personal_initial_capital NULL get
    null fields (dashboard renders '--').
    """
    with get_db() as conn:
        latest = conn.execute(
            "SELECT date, total_assets_hkd, nav FROM daily_nav "
            "ORDER BY date DESC LIMIT 1"
        ).fetchone()
        users = conn.execute(
            "SELECT id, username, is_admin, personal_initial_capital "
            "FROM users ORDER BY is_admin DESC, id"
        ).fetchall()

    if not latest:
        return jsonify({'has_data': False})

    nav = latest['nav']
    pool_initial = float(get_config('initial_capital', '1000000'))
    pool_current = latest['total_assets_hkd']

    user_rows = []
    for u in users:
        # Skip admins — the pool row covers them.
        if u['is_admin']:
            continue
        personal = u['personal_initial_capital']
        if personal is None:
            initial = current = pnl = None
        else:
            initial = float(personal)
            current = initial * nav
            pnl = current - initial
        user_rows.append({
            'id': u['id'],
            'username': u['username'],
            'initial': initial,
            'current': current,
            'pnl': pnl,
        })

    return jsonify({
        'has_data': True,
        'date': latest['date'],
        'nav': nav,
        'pool': {
            'initial': pool_initial,
            'current': pool_current,
            'pnl': pool_current - pool_initial,
        },
        'users': user_rows,
    })


# ─────────────────────────────────────────────
# Routes: API - Data Sync (trigger Futu data fetch)
# ─────────────────────────────────────────────
@app.route('/api/sync', methods=['POST'])
@admin_required
def api_sync():
    """Manually trigger data sync from Futu API.

    Response shape (back-compat: keep `ok`+`result` for older UIs):
      {'ok': bool, 'result': {...}, 'skipped': bool, 'accounts_ok': N,
       'accounts_total': N total, 'errors': [...]}

    `ok` is False when ─  a top-level exception aborted the run, OR every
    account failed and `sync_all_accounts` skipped writing a daily_nav row
    (so `result.skipped` is True). The admin UI turns this case red and
    lists the per-account errors, instead of showing a green "HKD 0".

    A *partial* failure (some accounts OK) still returns ok=True because a
    real daily_nav row was written; the failing accounts are surfaced via
    `result.accounts[].success=False` for display.
    """
    from futu_client import sync_all_accounts
    try:
        result = sync_all_accounts()
    except Exception as e:
        # Hard failure before/around the sync loop — nothing was written.
        return jsonify({'ok': False, 'error': str(e),
                        'skipped': True}), 500

    accounts = result.get('accounts', [])
    errors = [a.get('error') for a in accounts if not a.get('success')]
    skipped = bool(result.get('skipped'))
    # Every account failed → treat as not-ok so the UI alarms.
    any_ok = result.get('ok_count', 0) > 0
    return jsonify({
        'ok': (not skipped) and any_ok,
        'result': result,
        'skipped': skipped,
        'accounts_ok': result.get('ok_count', 0),
        'accounts_total': len(accounts),
        'errors': errors,
    })


# ─────────────────────────────────────────────
# Routes: Admin
# ─────────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_page():
    return render_template('admin.html',
                           username=session.get('username'))


@app.route('/api/admin/config', methods=['GET', 'POST'])
@admin_required
def api_admin_config():
    if request.method == 'GET':
        # Return config (mask sensitive values)
        futu_host = get_config('futu_host', '127.0.0.1')
        futu_port = get_config('futu_port', '11111')
        has_key = bool(get_config('futu_rsa_key_encrypted'))
        inception_date = get_config('inception_date', '2024-01-01')
        initial_capital = get_config('initial_capital', '1000000')

        # Account config
        accounts_json = get_config('accounts', '[]')
        accounts = json.loads(accounts_json)

        return jsonify({
            'futu_host': futu_host,
            'futu_port': futu_port,
            'has_rsa_key': has_key,
            'inception_date': inception_date,
            'initial_capital': initial_capital,
            'accounts': accounts
        })

    # POST - update config
    data = request.get_json()
    if 'futu_host' in data:
        set_config('futu_host', data['futu_host'])
    if 'futu_port' in data:
        set_config('futu_port', str(data['futu_port']))
    if 'futu_rsa_key' in data and data['futu_rsa_key']:
        set_config('futu_rsa_key_encrypted', encrypt_value(data['futu_rsa_key']))
    if 'inception_date' in data:
        set_config('inception_date', data['inception_date'])
    if 'initial_capital' in data:
        old_capital = float(get_config('initial_capital', '1000000'))
        new_capital = float(data['initial_capital'])
        set_config('initial_capital', str(new_capital))
        # `nav` and `pnl_pct` in every historical daily_nav row were computed
        # against the previous initial_capital. Changing the anchor without
        # rebasing history would leave the equity curve inconsistent (older
        # rows still divided by the old anchor). Recompute derived columns
        # from stored total_assets_hkd — the raw asset value never changes.
        if new_capital != old_capital and new_capital > 0:
            with get_db() as conn:
                conn.execute("""
                    UPDATE daily_nav
                       SET nav     = total_assets_hkd / ?,
                           pnl_pct = (total_assets_hkd / ? - 1.0) * 100
                """, (new_capital, new_capital))
    if 'accounts' in data:
        set_config('accounts', json.dumps(data['accounts']))

    return jsonify({'ok': True})


# ─────────────────────────────────────────────
# PBOC central-parity FX lookup + manual historical snapshot import
# ─────────────────────────────────────────────
# Rationale: the Futu OpenAPI cannot return historical NAV for a past
# date (accinfo_query is a live snapshot). To backfill the equity curve
# from monthly statements the admin punches in the raw native amount +
# FX rate per market. FX rates come from PBOC's daily central parity —
# authoritative and free to fetch, at the cost of scraping a Chinese
# government site that occasionally hiccups. Fall back to manual entry
# when the scrape fails.

_PBOC_LIST_URL = (
    'https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/'
    'index.html'
)
_PBOC_PAGE_URL = (
    'https://www.pbc.gov.cn/zhengcehuobisi/125207/125217/125925/'
    '17105-{page}.html'
)
_PBOC_UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36'


def _pboc_fetch(url):
    """HTTP GET with SSL verification relaxed (PBOC's cert chain is
    sometimes missing intermediates in headless containers)."""
    import urllib.request
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers={'User-Agent': _PBOC_UA})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
        return resp.read().decode('utf-8', 'replace')


def _pboc_find_detail_url(target_date):
    """Locate the detail-page URL for a target date on PBOC's paginated
    list. Pages are 20 items each, latest first, starting at page 1
    (which lives at index.html; subsequent pages at 17105-N.html).
    Returns the absolute detail URL or None if the date isn't present.

    target_date: 'YYYY-MM-DD'
    """
    import re
    y, m, d = target_date.split('-')
    needle = f'{int(y)}年{int(m)}月{int(d)}日'

    # Estimate the page number: newest entry is today, ~20 trading days
    # per page. Days behind divided by 20 gives a starting guess; then
    # we walk forwards/backwards a few pages to be safe.
    from datetime import date as _date
    try:
        target = _date.fromisoformat(target_date)
        days_behind = max(0, (_date.today() - target).days)
        # Only ~5 trading days per calendar week → ~20 per 4 weeks;
        # 20 entries per page → ~28 calendar days per page.
        start_page = max(1, days_behind // 28)
    except ValueError:
        start_page = 1

    # Probe up to 12 pages centered on the estimate (covers ~1 year of
    # date drift in either direction, plenty for statement backfill).
    seen = set()
    for offset in [0, 1, -1, 2, -2, 3, -3, 4, -4, 5, 6, 7]:
        page = start_page + offset
        if page < 1 or page in seen:
            continue
        seen.add(page)
        try:
            url = _PBOC_LIST_URL if page == 1 else _PBOC_PAGE_URL.format(page=page)
            html = _pboc_fetch(url)
        except Exception:
            continue
        if needle not in html:
            continue
        # Extract the detail-page relative link that immediately precedes
        # our date needle. Format:
        #   href="/zhengcehuobisi/.../2025123109021714424/index.html"
        idx = html.find(needle)
        window = html[max(0, idx - 800):idx]
        m = re.search(r'href="(/zhengcehuobisi/[^"]+/index\.html)"[^>]*>\s*$', window)
        if not m:
            # loosen — grab the last detail href in the window
            hrefs = re.findall(r'href="(/zhengcehuobisi/[^"]+/index\.html)"', window)
            if not hrefs:
                continue
            return 'https://www.pbc.gov.cn' + hrefs[-1]
        return 'https://www.pbc.gov.cn' + m.group(1)
    return None


def _pboc_parse_rates(detail_html):
    """Pull '1<currency>对人民币<rate>元' pairs from a PBOC detail page.
    Returns a {ccy_name: cny_per_unit} dict for the currencies we care
    about."""
    import re
    text = re.sub(r'<[^>]+>', ' ', detail_html)
    text = re.sub(r'\s+', ' ', text)
    rates = {}
    for ccy_zh, key in [
        ('美元', 'USD'),
        ('港元', 'HKD'),
        ('澳大利亚元', 'AUD'),
    ]:
        m = re.search(rf'1{ccy_zh}对人民币([\d.]+)元', text)
        if m:
            rates[key] = float(m.group(1))
    return rates


@app.route('/api/admin/pboc_parity')
@admin_required
def api_pboc_parity():
    """Look up USD→HKD and AUD→HKD (via CNY cross rate) from PBOC's
    daily central parity announcement for a given trading day.

    Query params:
      date=YYYY-MM-DD

    Returns:
      {
        date: 'YYYY-MM-DD',
        source_url: '...',
        rates: { HKD: 1.0, USD: <hkd_per_usd>, AUD: <hkd_per_aud> },
        pboc_raw: { USD_CNY, HKD_CNY, AUD_CNY }
      }
    or {error: '...'} on failure.
    """
    target = (request.args.get('date') or '').strip()
    if not target:
        return jsonify({'error': 'date required (YYYY-MM-DD)'}), 400
    try:
        detail_url = _pboc_find_detail_url(target)
    except Exception as e:
        return jsonify({'error': f'PBOC lookup failed: {e}'}), 502
    if not detail_url:
        return jsonify({
            'error': f'No PBOC announcement found for {target}. '
                     f'Not a trading day, or the page moved.'
        }), 404
    try:
        html = _pboc_fetch(detail_url)
    except Exception as e:
        return jsonify({'error': f'Fetch detail failed: {e}'}), 502
    raw = _pboc_parse_rates(html)
    if 'HKD' not in raw or raw['HKD'] <= 0:
        return jsonify({'error': 'Could not parse HKD/CNY rate from PBOC page'}), 500
    hkd_cny = raw['HKD']  # e.g. 1 HKD = 0.90322 CNY
    rates = {'HKD': 1.0}
    if 'USD' in raw:
        rates['USD'] = round(raw['USD'] / hkd_cny, 6)  # HKD per USD
    if 'AUD' in raw:
        rates['AUD'] = round(raw['AUD'] / hkd_cny, 6)  # HKD per AUD
    return jsonify({
        'date': target,
        'source_url': detail_url,
        'rates': rates,
        'pboc_raw': raw,
    })


@app.route('/api/admin/manual_snapshot', methods=['POST'])
@admin_required
def api_manual_snapshot():
    """Insert a historical snapshot into account_snapshots + daily_nav.

    Payload:
      {
        date: 'YYYY-MM-DD',
        entries: [
          { account_name: 'Futu HK',  market: 'HK', currency: 'HKD',
            total_native: 5127945.59, exchange_rate: 1.0 },
          { account_name: 'Moomoo US',market: 'US', currency: 'USD',
            total_native: 1620256.50, exchange_rate: 7.7822 },
          { account_name: 'Moomoo AU',market: 'AU', currency: 'AUD',
            total_native: 0,          exchange_rate: 5.5124 },
        ]
      }

    Uses INSERT OR REPLACE keyed on (date, account_name) so re-submitting
    the same month overwrites — matches the user's "fill 0 first, then
    replace when statements arrive" workflow.
    """
    data = request.get_json() or {}
    target_date = (data.get('date') or '').strip()
    entries = data.get('entries', [])
    if not target_date or not entries:
        return jsonify({'error': 'date and entries[] required'}), 400

    total_hkd = 0.0
    written_markets = []
    initial_capital = float(get_config('initial_capital', '1000000'))

    with get_db() as conn:
        for e in entries:
            name = (e.get('account_name') or '').strip()
            market = (e.get('market') or '').strip()
            ccy = (e.get('currency') or '').strip() or 'HKD'
            native = float(e.get('total_native') or 0)
            rate = float(e.get('exchange_rate') or 0)
            # Skip rows with no data. "Native amount == 0" means the user
            # has no statement for this market on this date — don't
            # write a zero row that would drag the aggregate down.
            # Non-zero amounts still require a valid rate.
            if not name or native <= 0 or rate <= 0:
                continue
            hkd = native * rate
            total_hkd += hkd
            written_markets.append(market or name)
            conn.execute("""
                INSERT OR REPLACE INTO account_snapshots
                    (date, account_name, market, currency,
                     total_assets, exchange_rate, total_assets_hkd)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (target_date, name, market, ccy, native, rate, hkd))

        # If no market contributed a non-zero value, skip the daily_nav
        # row entirely — the equity curve should only pass through days
        # for which we have at least one real observation.
        if not written_markets:
            return jsonify({
                'ok': True,
                'date': target_date,
                'skipped': True,
                'reason': 'All markets are empty; no snapshot written.',
            })

        nav = total_hkd / initial_capital if initial_capital > 0 else 1.0
        pnl_pct = (nav - 1.0) * 100
        conn.execute("""
            INSERT OR REPLACE INTO daily_nav (date, total_assets_hkd, nav, pnl_pct)
            VALUES (?, ?, ?, ?)
        """, (target_date, total_hkd, nav, pnl_pct))

    return jsonify({
        'ok': True,
        'date': target_date,
        'total_hkd': total_hkd,
        'nav': nav,
        'pnl_pct': pnl_pct,
        'markets_written': written_markets,
    })


@app.route('/api/admin/users', methods=['GET', 'POST', 'DELETE'])
@admin_required
def api_admin_users():
    if request.method == 'GET':
        with get_db() as conn:
            users = conn.execute(
                "SELECT id, username, is_admin, personal_initial_capital, "
                "created_at FROM users"
            ).fetchall()
        return jsonify([dict(u) for u in users])

    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        is_admin = data.get('is_admin', False)
        # Optional per-user anchor. Admins don't need one (they see the
        # pool figure); regular users see '--' until this is set.
        raw_cap = data.get('personal_initial_capital', None)
        try:
            personal_cap = float(raw_cap) if raw_cap not in (None, '', 0) else None
        except (TypeError, ValueError):
            personal_cap = None
        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, is_admin, "
                    "personal_initial_capital) VALUES (?,?,?,?)",
                    (username, generate_password_hash(password),
                     int(is_admin), personal_cap)
                )
            return jsonify({'ok': True})
        except sqlite3.IntegrityError:
            return jsonify({'error': 'Username already exists'}), 400

    if request.method == 'DELETE':
        data = request.get_json()
        user_id = data.get('user_id')
        if user_id == session.get('user_id'):
            return jsonify({'error': 'Cannot delete yourself'}), 400
        with get_db() as conn:
            conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        return jsonify({'ok': True})


@app.route('/api/admin/user_capital', methods=['POST'])
@admin_required
def api_admin_user_capital():
    """Update a single user's personal_initial_capital.

    Pass `null` (or an empty string) to clear it — the user's dashboard
    will fall back to '--' for the amount fields. Only affects that
    user's numeric display; nothing in daily_nav or config is touched.
    """
    data = request.get_json()
    user_id = data.get('user_id')
    raw = data.get('personal_initial_capital', None)
    if user_id is None:
        return jsonify({'error': 'user_id required'}), 400
    try:
        capital = float(raw) if raw not in (None, '', 0) else None
    except (TypeError, ValueError):
        return jsonify({'error': 'invalid amount'}), 400
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET personal_initial_capital=? WHERE id=?",
            (capital, user_id)
        )
    return jsonify({'ok': True})


@app.route('/api/admin/change_password', methods=['POST'])
@admin_required
def api_change_password():
    data = request.get_json()
    user_id = data.get('user_id')
    new_password = data.get('new_password')
    if not new_password:
        return jsonify({'error': 'Password required'}), 400
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET password_hash=? WHERE id=?",
            (generate_password_hash(new_password), user_id)
        )
    return jsonify({'ok': True})


# ─────────────────────────────────────────────
# Initialize
# ─────────────────────────────────────────────
init_db()

# Start the auto-sync scheduler (weekday 16:30 HKT + Sat 08:00 HKT).
# Guarded by SCHEDULER_ENABLED so a multi-worker gunicorn deploy can
# pin the scheduler to one worker; defaults on for the single-worker
# `python app.py` container.
if os.environ.get('SCHEDULER_ENABLED', '1') == '1':
    try:
        from scheduler import start_scheduler
        start_scheduler()
    except Exception as e:
        print(f"[WARN] scheduler failed to start: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
