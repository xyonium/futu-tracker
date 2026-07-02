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
# Historical note: earlier revisions of this file did a separate FX lookup
# against exchangerate.host to convert account balances into HKD. We now
# rely on OpenD's server-side conversion (accinfo_query supports a
# `currency` argument) so no external FX API is needed for the sync path.
# The exchange rate stored in each snapshot is the *implied* HKD/native
# ratio that OpenD returned, not a rate we fetched ourselves.


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


def _rsa_key_to_tempfile():
    """
    Materialise the DB-stored RSA private key to a tempfile so the futu
    SDK can point `SysConfig.set_init_rsa_file` at it.

    Futu proto encryption is a **shared private key** scheme, not asymmetric:
    OpenD (via <rsa_private_key> in FutuOpenD.xml) and every SDK client
    load the *same* PKCS#1 1024-bit private key. Do not try to feed a
    public key here — the SDK's `_read_rsa_keys` refuses with "This is
    not a private key". See docs/opend-setup.md for the underlying
    handshake spec (documented in Chinese as "要求1024位, 格式为PKCS#1").
    """
    import tempfile
    key = decrypt_rsa_key()
    if not key:
        return None
    key = key.strip() + "\n"
    tmp = tempfile.NamedTemporaryFile(
        mode='w', suffix='.pem', delete=False, dir='/tmp'
    )
    tmp.write(key)
    tmp.close()
    os.chmod(tmp.name, 0o600)
    return tmp.name


_SDK_CONFIGURED = False


def _configure_sdk_encryption():
    """
    One-time setup: enable proto encryption + point SDK at the shared
    RSA private key.
    """
    global _SDK_CONFIGURED
    if _SDK_CONFIGURED:
        return
    import futu as ft
    key_path = _rsa_key_to_tempfile()
    if key_path is None:
        raise RuntimeError(
            "No RSA private key configured. OpenD auto-generates one on "
            "first boot — run `docker compose logs opend | grep -A20 "
            "'NEW RSA PRIVATE KEY'` and paste the block into the tracker "
            "admin panel."
        )
    ft.SysConfig.enable_proto_encrypt(is_encrypt=True)
    ft.SysConfig.set_init_rsa_file(key_path)
    _SDK_CONFIGURED = True


def _open_context(host, port, firm_name):
    """
    Open an OpenSecTradeContext for a given SecurityFirm, with the widest
    market filter so client-side account matching sees everything.
    """
    import futu as ft
    _configure_sdk_encryption()
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

    OpenD's `accinfo_query` accepts a `currency` argument and returns
    `total_assets` already converted to that currency using Futu's own
    daily mid-rate — so we ask for HKD directly and avoid a separate FX
    lookup. We also grab the native-currency figure for display purposes
    (so the account row can still show "AUD 482,264").

    Returns a dict:
      {
        'total_hkd': float,      # HKD-denominated total (Futu's rate)
        'total_native': float,   # same account, in its native currency
        'native_currency': str,  # e.g. 'AUD'
        'fx_rate': float,        # implied HKD-per-native rate
      }

    `acc_id` may be empty string, in which case the first account matching
    `market` for the target SecurityFirm is used. It also accepts the
    human-facing card number (what the Futu app shows) — see the matching
    block below.
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

        target_acc = None
        if acc_id:
            # Users typically enter their Futu account card number (e.g.
            # "1001295093909611") — the same one shown in the Futu app.
            # The SDK's `acc_id` is a distinct 18-digit internal ID
            # (e.g. "281756481028617187"), while `card_num` / `uni_card_num`
            # hold the human-facing card number. Match against any of them
            # so either form works.
            wanted = str(acc_id).strip()
            for _, row in acc_list.iterrows():
                candidates = {
                    str(row.get('acc_id', '') or '').strip(),
                    str(row.get('uni_card_num', '') or '').strip(),
                    str(row.get('card_num', '') or '').strip(),
                }
                if wanted in candidates:
                    target_acc = row
                    break
        else:
            # First REAL account whose trdmarket_auth includes the target market
            for _, row in acc_list.iterrows():
                if str(row.get('trd_env', '')) != 'REAL':
                    continue
                if str(row.get('acc_status', '')) != 'ACTIVE':
                    continue
                auth = str(row.get('trdmarket_auth', '') or '').upper()
                if market in auth:
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

        # Query 1 — total assets in HKD. Futu applies its own daily FX rate
        # server-side so we skip any external exchangerate.host lookup.
        hkd_enum = getattr(ft.Currency, 'HKD', None)
        kwargs_hkd = dict(trd_env=env, acc_id=acc_id_int)
        if hkd_enum is not None:
            kwargs_hkd['currency'] = hkd_enum
        ret, funds_hkd = trd_ctx.accinfo_query(**kwargs_hkd)
        if ret != ft.RET_OK:
            raise Exception(f"accinfo_query(HKD) failed: {funds_hkd}")
        total_hkd = float(funds_hkd['total_assets'].iloc[0])

        # Query 2 — same account in its native currency (for display).
        # Some sub-accounts refuse foreign currency conversion (e.g. HK-only
        # cash accounts return "该账户不支持换算此币种"). Fall back to the
        # HKD figure with a 1.0 fx_rate when that happens.
        native_ccy_name = _MARKET_CURRENCY.get(market, 'HKD')
        total_native = total_hkd
        native_currency = 'HKD'
        fx_rate = 1.0

        if native_ccy_name != 'HKD':
            native_enum = getattr(ft.Currency, native_ccy_name, None)
            if native_enum is not None:
                kwargs_native = dict(trd_env=env, acc_id=acc_id_int, currency=native_enum)
                ret, funds_native = trd_ctx.accinfo_query(**kwargs_native)
                if ret == ft.RET_OK:
                    total_native = float(funds_native['total_assets'].iloc[0])
                    reported_ccy = str(funds_native['currency'].iloc[0]) \
                        if 'currency' in funds_native.columns else ''
                    native_currency = reported_ccy if reported_ccy not in ('', 'nan', 'None') \
                        else native_ccy_name
                    if total_native > 0:
                        fx_rate = total_hkd / total_native

        return {
            'total_hkd': total_hkd,
            'total_native': total_native,
            'native_currency': native_currency,
            'fx_rate': fx_rate,
        }

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
            bal = get_account_balance(
                host, port,
                acct.get('trd_env', 'REAL'),
                acct.get('acc_id', ''),
                acct['market']
            )
            assets_hkd = bal['total_hkd']
            total_native = bal['total_native']
            currency = bal['native_currency']
            rate = bal['fx_rate']
            total_hkd += assets_hkd

            # Save snapshot — `total_assets` is in the account's native
            # currency, `total_assets_hkd` is what Futu itself reports for
            # HKD conversion, `exchange_rate` is the implied HKD-per-native
            # rate (useful for a "how did today's FX move?" column).
            with get_db() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO account_snapshots
                    (date, account_name, market, currency, total_assets, exchange_rate, total_assets_hkd)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (target_date, acct['name'], acct['market'], currency,
                      total_native, rate, assets_hkd))

            results.append({
                'account': acct['name'],
                'market': acct['market'],
                'total_assets': total_native,
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
