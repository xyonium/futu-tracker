#!/usr/bin/env python3
"""
Futu Portfolio Tracker - Main Application
Multi-account portfolio tracking with currency conversion and NAV calculation.
"""

import os
import json
import hashlib
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
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        if not session.get('is_admin'):
            if request.is_json:
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

    with get_db() as conn:
        rows = conn.execute(
            "SELECT date, total_assets_hkd, nav, pnl_pct FROM daily_nav "
            "WHERE date >= ? ORDER BY date",
            (start,)
        ).fetchall()

    return jsonify({
        'inception_date': inception_date,
        'initial_capital': float(get_config('initial_capital', '1000000')),
        'data': [dict(r) for r in rows]
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
    """Current portfolio summary."""
    with get_db() as conn:
        latest = conn.execute(
            "SELECT * FROM daily_nav ORDER BY date DESC LIMIT 1"
        ).fetchone()
    if not latest:
        return jsonify({'has_data': False})

    initial_capital = float(get_config('initial_capital', '1000000'))
    return jsonify({
        'has_data': True,
        'date': latest['date'],
        'total_assets_hkd': latest['total_assets_hkd'],
        'nav': latest['nav'],
        'pnl_pct': latest['pnl_pct'],
        'initial_capital': initial_capital,
        'pnl_amount': latest['total_assets_hkd'] - initial_capital
    })


# ─────────────────────────────────────────────
# Routes: API - Data Sync (trigger Futu data fetch)
# ─────────────────────────────────────────────
@app.route('/api/sync', methods=['POST'])
@admin_required
def api_sync():
    """Manually trigger data sync from Futu API."""
    from futu_client import sync_all_accounts
    try:
        result = sync_all_accounts()
        return jsonify({'ok': True, 'result': result})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


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
        set_config('initial_capital', str(data['initial_capital']))
    if 'accounts' in data:
        set_config('accounts', json.dumps(data['accounts']))

    return jsonify({'ok': True})


@app.route('/api/admin/users', methods=['GET', 'POST', 'DELETE'])
@admin_required
def api_admin_users():
    if request.method == 'GET':
        with get_db() as conn:
            users = conn.execute(
                "SELECT id, username, is_admin, created_at FROM users"
            ).fetchall()
        return jsonify([dict(u) for u in users])

    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '')
        is_admin = data.get('is_admin', False)
        if not username or not password:
            return jsonify({'error': 'Username and password required'}), 400
        try:
            with get_db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,?)",
                    (username, generate_password_hash(password), int(is_admin))
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

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
