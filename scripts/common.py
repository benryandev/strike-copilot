"""Shared helpers: paths, HTTP (curl, so it works where Python's SSL store doesn't), profile, markets, fee rate."""
import json, subprocess, time, datetime, os, sys
from pathlib import Path

F = float
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
PROFILE = ROOT / 'profile.json'
STRIKE_API = 'https://api.strikefinance.org'
HL_API = 'https://api.hyperliquid.xyz/info'
SLIP = 0.0010   # copy-cost allowance per close (HL/Strike price gap, copy delay, market-order closes); calibrated against a real copy account
COPIER_CAP = 40  # Strike's limit on active copiers per lead
DAY = 86_400_000


def now_ms(): return int(time.time() * 1000)
def day(ms): return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).strftime('%Y-%m-%d')


def _curl(args):
    return subprocess.run(['curl', '-s', '--max-time', '30', *args], capture_output=True, text=True).stdout


def get(url, tries=3):
    """Public GET returning parsed JSON ({} after repeated failures)."""
    if url.startswith('/'): url = STRIKE_API + url
    for i in range(tries):
        try: return json.loads(_curl([url]))
        except ValueError: time.sleep(1 + i)
    return {}


def hl(body, want=None):
    """Hyperliquid info call. HL rate-limits silently (null or non-JSON reply), so retry with backoff and never return a throttled reply as data."""
    for wait in (0, 2, 5, 10, 20, 40):
        time.sleep(wait)
        try: out = json.loads(_curl(['-X', 'POST', HL_API, '-H', 'Content-Type: application/json', '-d', json.dumps(body)]))
        except ValueError: continue
        if out is not None and (want is None or isinstance(out, want)): return out
    raise RuntimeError(f"Hyperliquid gave no answer for {body.get('type')} after retries (rate limit?). Wait a minute and run it again.")


# ---------- profile (answers from the setup questions; gitignored) ----------
DEFAULTS = {'balance': None, 'leverage': 10, 'max_use': 0.5, 'max_dd': 0.25, 'excluded': [], 'sub_account_id': None,
            'account_id': None, 'review_day': 'Monday', 'mode_pref': 'either', 'referral_asked': None, 'margin_mode': 'isolated'}


def load_profile(required=True):
    if not PROFILE.exists():
        if required: sys.exit('No profile yet. Ask Claude to "set me up" (or run: python3 scripts/profile.py set balance=1000).')
        return dict(DEFAULTS)
    return {**DEFAULTS, **json.loads(PROFILE.read_text())}


def save_profile(p): PROFILE.write_text(json.dumps(p, indent=1) + '\n')


# ---------- markets ----------
# Strike base asset -> Hyperliquid coin, where the names differ. Everything else maps by name (core) or "xyz:<name>" (HIP-3 stocks/commodities).
ALIASES = {'SKHYNIX': 'xyz:SKHX', 'NAS100': 'xyz:XYZ100', 'WTI': 'xyz:CL', 'XAG': 'xyz:SILVER', 'XAU': 'xyz:GOLD'}
LIQUID_USD = 100_000  # a Strike market with less than this 24h volume is thin: copies there slip more


def markets(refresh=False):
    """Strike's listed markets with their HL twin, minimum order and 24h volume. Cached for 12h in data/markets.json."""
    cache = DATA / 'markets.json'
    if not refresh and cache.exists() and time.time() - cache.stat().st_mtime < 12 * 3600:
        return json.loads(cache.read_text())
    mk = get('/v2/markets').get('markets', {})
    if not mk: sys.exit('Could not read Strike markets (/v2/markets). Check your internet connection.')
    core = {u['name'] for u in hl({'type': 'meta'}, dict)['universe']}
    xyz = {u['name'] for u in hl({'type': 'meta', 'dex': 'xyz'}, dict)['universe']}
    vol = {t['symbol']: F(t['quoteVolume']) for t in (get('/price/v2/ticker/24hr') or []) if isinstance(t, dict)}
    out = {}
    for sym, m in mk.items():
        if m.get('status') != 'trading': continue
        b = m['base_asset']
        coin = ALIASES.get(b) or (b if b in core else f'xyz:{b}' if f'xyz:{b}' in xyz else None)
        out[sym] = {'base': b, 'hl': coin, 'min_notional': F(m.get('order_min_notional') or 10), 'vol24h': vol.get(sym, 0.0)}
    DATA.mkdir(exist_ok=True); cache.write_text(json.dumps(out, indent=1))
    return out


def hl_map(mk=None):
    """HL coin -> Strike symbol (e.g. 'xyz:GOLD' -> 'XAU-USD') for every Strike market a Hyperliquid lead can be copied on."""
    return {v['hl']: s for s, v in (mk or markets()).items() if v['hl']}


# ---------- fees ----------
def fee_rate(account_id):
    """Your taker fee per side after Strike's volume tier, staking discount and referral discount, read from your public portfolio."""
    pf = get(f'/v2/portfolio?account_id={account_id}')
    tiers = {t['Tier']: F(t['TakerRate']) for t in pf.get('fee_tiers', [])}
    base = tiers.get(pf.get('fee_tier', 0), 0.0005)
    stake = F(pf.get('staking_fee_discount_rate') or 0); ref = F(pf.get('fee_discount_rate') or 0) / 100
    return base * (1 - stake) * (1 - ref), dict(tier=pf.get('fee_tier', 0), base=base, staking=stake, referral=ref)


def my_fee():
    """Fee for the profile's account, or Strike's base taker fee (0.05%) when no account is linked yet."""
    p = load_profile(required=False)
    return fee_rate(p['account_id'])[0] if p.get('account_id') else 0.0005
