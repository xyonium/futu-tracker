#!/usr/bin/env python3
"""
Futu OpenD API Client - Fetches account balances and calculates NAV.

Requires a running Futu OpenD gateway (see docs/opend-setup.md for the
Docker deployment). This module talks to it over TCP.

Design notes (aligned with the official skill pack at
https://openapi.futunn.com/futu-api-doc/intro/ai.html):

- Use ONE `OpenSecTradeContext` with `filter_trdmarket=TrdMarket.NONE` and
  iterate through the returned account list rather than opening a fresh
  context per market. OpenD gets slow (or errors out) when a client rapidly
  opens/closes multiple trade contexts.
- Do NOT rely on constructor-side market filtering to pick an account —
  filter client-side against `trdmarket_auth` in the account row. Some SDK
  versions drop accounts whose primary market doesn't match the passed
  `filter_trdmarket`, hiding legitimate multi-market accounts.
- `SecurityFirm` still has to match the account's broker (FUTUSECURITIES
  for HK, FUTUINC for US, FUTUAU for AU), so we open one context per firm.
- `acc_id` from `get_acc_list()` is an 18-digit int — preserve it as a
  Python int, don't round-trip through float.
"""

import json
import sqlite3
import os
import requests
from datetime import datetime, date
from contextlib import contextmanager

# futu-api is imported at runtime — see get_account_balance / sync_all_accounts
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
        if target_date:
            url = f"https://api.exchangerate.host/{target_date}?base={currency}&symbols=HKD"
        else:
            url = f"https://api.exchangerate.host/latest?base={currency}&symbols=HKD"
        resp = requests.get(url, timeout=10)
        rate = float(resp.json()['rates']['HKD'])
        RATE_CACHE[cache_key] = rate
        return rate
    except Exception as e:
        print(f"[FX] Fallback rate for {currency}: {e}")
        # Fallback approximate rates
        return {'USD': 7.8, 'AUD': 5.1, 'CNY': 1.1}.get(currency, 1.0)


# ─────────────────────────────────────────────
# Futu OpenD Connection
# ─────────────────────────────────────────────
# Map "market" as configured in the admin UI -> the SecurityFirm that owns
# accounts for that market. This is authoritative per FUTU_SECURITY_FIRM
# in the official skill pack (docs/API_REFERENCE.md).
_MARKET_TO_FIRM = {
    'HK': 'FUTUSECURITIES',
    'US': 'FUTUINC',
    'AU': 'FUTUAU',
    'SG': 'FUTUSG',
    'JP': 'FUTUJP',
    'MY': 'FUTUMY',
    'CA': 'FUTUCA',
}

# Currency defaults used when accinfo_query doesn't report currency explicitly.
_MARKET_CURRENCY = {
    'HK': 'HKD',
    'US': 'USD',
    'AU': 'AUD',
    'SG': 'SGD',
    'JP': 'JPY',
    'MY': 'MYR',
    'CA': 'CAD',
    'CN': 'CNY',
}


def _open_context(host, port, firm_name):
    """
    Open an OpenSecTradeContext for a given SecurityFirm, with the widest
    market filter so client-side account matching sees everything.
    """
    import futu as ft
    firm = getattr(ft.SecurityFirm, firm_name, None)
    if firm is None:
        raise ValueError(
            f"SDK doesn't know SecurityFirm.{firm_name} — upgrade futu-api "
            f"(pip install --upgrade 'futu-api>=10.4.6408')"
        )
    return ft.OpenSecTradeContext(
        host=host,
        port=int(port),
        security_firm=firm,
        filter_trdmarket=ft.TrdMarket.NONE,   # widest filter — matches skill guidance
        is_encrypt=True,                      # required when OpenD listens on 0.0.0.0
    )


def get_account_balance(host, port, trd_env, acc_id, market):
    """
    Connect to Futu OpenD and fetch a single account's total assets.

    Returns (total_assets: float, currency: str).

    `acc_id` may be empty string, in which case the first account matching
    `market` for the target SecurityFirm is used.
    """
    import futu as ft

    firm_name = _MARKET_TO_FIRM.get(market)
    if firm_name is None:
        raise ValueError(f"Unknown market: {market}")

    trd_ctx = _open_context(host, port, firm_name)
    try:
        # Optionally unlock trade if an RSA key was configured. This is only
        # required for order-placing operations; for balance queries alone
        # you can leave it disabled. Skill guidance is to prefer unlocking
        # via the OpenD GUI, but we honour the DB-stored key when present.
        rsa_key = decrypt_rsa_key()
        if rsa_key:
            trd_ctx.unlock_trade(password='', password_md5=None, is_unlock=True)

        ret, acc_list = trd_ctx.get_acc_list()
        if ret != ft.RET_OK:
            raise Exception(f"get_acc_list failed: {acc_list}")

        # Client-side account matching. `trdmarket_auth` is a comma/list
        # field on newer SDKs — normalize to a string search.
        def _row_matches_market(row):
            auth = str(row.get('trdmarket_auth', '') or '')
            return market in auth.upper()

        target_acc = None
        if acc_id:
            for _, row in acc_list.iterrows():
                if str(row['acc_id']) == str(acc_id):
                    target_acc = row
                    break
        else:
            # First account whose trdmarket_auth includes the target market
            for _, row in acc_list.iterrows():
                if _row_matches_market(row):
                    target_acc = row
                    break
            # Fallback: first account of any market (single-market users)
            if target_acc is None and len(acc_list) > 0:
                target_acc = acc_list.iloc[0]

        if target_acc is None:
            raise Exception(
                f"No account found for market={market} firm={firm_name} "
                f"acc_id={acc_id or '(auto)'}. Check that the sub-account "
                f"is opened in Futu."
            )

        # Preserve acc_id precision (18-digit ints lose precision through float)
        acc_id_int = int(target_acc['acc_id'])
        env = ft.TrdEnv.REAL if trd_env == 'REAL' else ft.TrdEnv.SIMULATE

        # Ask for balance in the account's native currency — cleaner than
        # letting the SDK auto-pick. Default to HKD if unmapped.
        native_ccy_name = _MARKET_CURRENCY.get(market, 'HKD')
        currency_enum = getattr(ft.Currency, native_ccy_name, None)

        query_kwargs = dict(trd_env=env, acc_id=acc_id_int)
        if currency_enum is not None:
            query_kwargs['currency'] = currency_enum

        ret, funds = trd_ctx.accinfo_query(**query_kwargs)
        if ret != ft.RET_OK:
            raise Exception(f"accinfo_query failed: {funds}")

        total_assets = float(funds['total_assets'].iloc[0])

        # Currency: prefer response field, fall back to market default.
        if 'currency' in funds.columns:
            currency = str(funds['currency'].iloc[0])
        else:
            currency = ''
        if currency in ('nan', '', 'None'):
            currency = native_ccy_name

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

    # Prefer explicit env overrides (set by docker-compose so first-run works
    # before the admin has clicked through the settings page). Falls back to
    # DB config, then to localhost defaults.
    host = os.environ.get('FUTU_HOST') or get_config('futu_host', '127.0.0.1')
    port = os.environ.get('FUTU_PORT') or get_config('futu_port', '11111')
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
                    (date, account_name, market, currency, total_assets, exchange_rate_to_hkd, total_assets_hkd)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (target_date, acct['name'], acct['market'], currency,
                      total_assets, rate, assets_hkd))

            results.append({
                'account': acct['name'],
                'market': acct['market'],
                'total_assets': total_assets,
                'currency': currency,
                'rate': rate,
                'total_hkd': assets_hkd,
                'success': True,
            })
        except Exception as e:
            results.append({
                'account': acct['name'],
                'market': acct['market'],
                'success': False,
                'error': str(e),
            })

    # Save daily NAV
    nav = total_hkd / initial_capital if initial_capital > 0 else 1.0
    pnl_pct = (nav - 1.0) * 100

    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO daily_nav (date, total_assets_hkd, nav, pnl_pct)
            VALUES (?, ?, ?, ?)
        """, (target_date, total_hkd, nav, pnl_pct))

    return {
        'date': target_date,
        'total_hkd': total_hkd,
        'nav': nav,
        'pnl_pct': pnl_pct,
        'accounts': results,
    }


if __name__ == '__main__':
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    result = sync_all_accounts(target)
    print(json.dumps(result, indent=2, default=str))
