#!/usr/bin/env python3
"""
Futu OpenD API Client - Fetches account balances and calculates NAV.
Requires futu-api package and a running OpenD gateway.
"""

import json
import sqlite3
import os
import requests
from datetime import datetime, date
from contextlib import contextmanager

# futu-api will be imported at runtime
# import futu as ft

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'portfolio.db')
ENCRYPTION_KEY_FILE = os.path.join(os.path.dirname(__file__), 'data', '.encryption_key')


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_config(key, default=None):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default


def decrypt_rsa_key():
    from cryptography.fernet import Fernet
    encrypted = get_config('futu_rsa_key_encrypted')
    if not encrypted:
        return None
    with open(ENCRYPTION_KEY_FILE, 'rb') as f:
        key = f.read()
    return Fernet(key).decrypt(encrypted.encode()).decode()


# ─────────────────────────────────────────────
# Exchange Rate (mid-rate)
# ─────────────────────────────────────────────
# Currency mapping: account currency -> HKD
RATE_CACHE = {}

def get_exchange_rate_to_hkd(currency: str, target_date: str = None) -> float:
    """
    Get exchange rate: 1 unit of `currency` = ? HKD.
    Uses exchangerate.host or similar free API.
    """
    currency = currency.upper()
    if currency == 'HKD':
        return 1.0

    cache_key = f"{currency}_{target_date or 'latest'}"
    if cache_key in RATE_CACHE:
        return RATE_CACHE[cache_key]

    try:
        # Primary: exchangerate-api (free tier)
        if target_date:
            url = f"https://api.exchangerate.host/{target_date}?base={currency}&symbols=HKD"
        else:
            url = f"https://api.exchangerate.host/latest?base={currency}&symbols=HKD"

        resp = requests.get(url, timeout=10)
        data = resp.json()
        if data.get('success') and 'rates' in data:
            rate = data['rates'].get('HKD', 1.0)
            RATE_CACHE[cache_key] = rate
            return rate
    except Exception:
        pass

    # Fallback: approximate static rates
    fallback_rates = {
        'USD': 7.82,
        'AUD': 5.10,
        'CNY': 1.08,
        'SGD': 5.80,
        'GBP': 9.90,
        'EUR': 8.50,
    }
    rate = fallback_rates.get(currency, 1.0)
    RATE_CACHE[cache_key] = rate
    return rate


# ─────────────────────────────────────────────
# Futu API Connection
# ─────────────────────────────────────────────
def get_account_balance(host, port, trd_env, acc_id, market):
    """
    Connect to Futu OpenD and fetch account total assets.
    Returns (total_assets, currency).
    """
    import futu as ft

    rsa_key = decrypt_rsa_key()

    if market == 'HK':
        trd_ctx = ft.OpenSecTradeContext(
            host=host, port=int(port), security_firm=ft.SecurityFirm.FUTUSECURITIES,
            filter_trdmarket=ft.TrdMarket.HK
        )
    elif market == 'US':
        trd_ctx = ft.OpenSecTradeContext(
            host=host, port=int(port), security_firm=ft.SecurityFirm.FUTUINC,
            filter_trdmarket=ft.TrdMarket.US
        )
    elif market == 'AU':
        trd_ctx = ft.OpenSecTradeContext(
            host=host, port=int(port), security_firm=ft.SecurityFirm.FUTUAU,
            filter_trdmarket=ft.TrdMarket.AU
        )
    else:
        raise ValueError(f"Unknown market: {market}")

    try:
        # Unlock trade if RSA key exists
        if rsa_key:
            ret, data = trd_ctx.unlock_trade(password='', is_unlock=True)

        # Get account list
        ret, acc_list = trd_ctx.get_acc_list()
        if ret != ft.RET_OK:
            raise Exception(f"get_acc_list failed: {acc_list}")

        # Find matching account
        target_acc = None
        for _, row in acc_list.iterrows():
            if acc_id and str(row['acc_id']) == str(acc_id):
                target_acc = row
                break
        if target_acc is None and not acc_id:
            # Use first matching market account
            target_acc = acc_list.iloc[0] if len(acc_list) > 0 else None

        if target_acc is None:
            raise Exception(f"Account not found for market {market}")

        # Get funds
        ret, funds = trd_ctx.accinfo_query(
            trd_env=ft.TrdEnv.REAL if trd_env == 'REAL' else ft.TrdEnv.SIMULATE,
            acc_id=int(target_acc['acc_id'])
        )
        if ret != ft.RET_OK:
            raise Exception(f"accinfo_query failed: {funds}")

        total_assets = float(funds['total_assets'].iloc[0])
        currency = str(funds.get('currency', {}).iloc[0]) if 'currency' in funds.columns else 'HKD'

        # Determine currency from market if not in response
        if currency in ('nan', '', 'None'):
            market_currency = {'HK': 'HKD', 'US': 'USD', 'AU': 'AUD'}
            currency = market_currency.get(market, 'HKD')

        return total_assets, currency

    finally:
        trd_ctx.close()


# ─────────────────────────────────────────────
# Sync all accounts
# ─────────────────────────────────────────────
def sync_all_accounts(target_date=None):
    """
    Fetch balances from all configured accounts,
    convert to HKD, calculate NAV, and save to DB.
    """
    if target_date is None:
        target_date = date.today().isoformat()

    host = get_config('futu_host', '127.0.0.1')
    port = get_config('futu_port', '11111')
    inception_date = get_config('inception_date', '2024-01-01')
    initial_capital = float(get_config('initial_capital', '1000000'))

    if target_date < inception_date:
        return {'skipped': True, 'reason': 'Before inception date'}

    # Load account configurations
    accounts_json = get_config('accounts', '[]')
    accounts = json.loads(accounts_json)

    if not accounts:
        # Default accounts if none configured
        accounts = [
            {'name': 'Futu HK', 'market': 'HK', 'acc_id': '', 'trd_env': 'REAL'},
            {'name': 'Moomoo US', 'market': 'US', 'acc_id': '', 'trd_env': 'REAL'},
            {'name': 'Moomoo AU', 'market': 'AU', 'acc_id': '', 'trd_env': 'REAL'},
        ]

    results = []
    total_hkd = 0.0

    for acct in accounts:
        try:
            total_assets, currency = get_account_balance(
                host, port,
                acct.get('trd_env', 'REAL'),
                acct.get('acc_id', ''),
                acct['market']
            )
            rate = get_exchange_rate_to_hkd(currency, target_date)
            assets_hkd = total_assets * rate
            total_hkd += assets_hkd

            # Save snapshot
            with get_db() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO account_snapshots
                    (date, account_name, currency, total_assets, total_assets_hkd, exchange_rate)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (target_date, acct['name'], currency, total_assets, assets_hkd, rate))

            results.append({
                'account': acct['name'],
                'currency': currency,
                'total_assets': total_assets,
                'rate': rate,
                'assets_hkd': assets_hkd,
                'status': 'ok'
            })
        except Exception as e:
            results.append({
                'account': acct['name'],
                'status': 'error',
                'error': str(e)
            })

    # Calculate NAV
    if total_hkd > 0:
        nav = total_hkd / initial_capital
        pnl_pct = (nav - 1.0) * 100

        with get_db() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO daily_nav (date, total_assets_hkd, nav, pnl_pct)
                VALUES (?, ?, ?, ?)
            """, (target_date, total_hkd, nav, pnl_pct))

    return {
        'date': target_date,
        'total_hkd': total_hkd,
        'nav': total_hkd / initial_capital if total_hkd > 0 else 0,
        'accounts': results
    }


if __name__ == '__main__':
    result = sync_all_accounts()
    print(json.dumps(result, indent=2))
