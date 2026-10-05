"""Strike API-wallet helper (Ed25519). Your private key lives in .secrets/strike_api.key on this computer only.

  python3 scripts/strike_api.py keygen        create a key; prints the PUBLIC key to paste on app.strikefinance.org/api-keys
  python3 scripts/strike_api.py pubkey        print the public key again
  python3 scripts/strike_api.py whoami        check the key works: account id, balance, sub-accounts
  python3 scripts/strike_api.py get <path>    any authenticated read, e.g. get "/v2/positions?sub_account_id=..."

Changes (each prints the exact request and does nothing unless you add --yes):
  set-leverage <10> [SYM-USD,...|all] [sub=<id>]           leverage for FUTURE positions (a market with an open position keeps its old setting)
  set-margin-mode <isolated|cross> [SYM-USD,...|all] [sub=<id>]
  subscribe <lead> margin=<usd>|ratio=<usd> [platform=hyperliquid|strike] [cap=<usd/symbol>] [exclude=A-USD,B-USD] [sub=<id>]
  caps <subscription_id> <per_symbol_usd> <total_usd> [exclude=A-USD,...] [sub=<id>]     ("0" clears a cap)
  stop <subscription_id> close|keep [sub=<id>]             close = close copied positions at market; keep = leave them open

What this tool can never do: place, change or cancel your own orders, withdraw, or transfer funds.
Strike API keys can't withdraw by design, and order endpoints are deliberately not wired here.
"""
import hashlib, json, os, sys, time, uuid, subprocess
from pathlib import Path
from common import STRIKE_API, ROOT, markets

KEYFILE = Path(os.environ.get('STRIKE_KEYFILE') or ROOT / '.secrets' / 'strike_api.key')  # env override: keep the key somewhere else


def keygen():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    if KEYFILE.exists():
        sys.exit(f'A key already exists at {KEYFILE}. To make a new one, delete that file first (and remove the old key on Strike).')
    priv = Ed25519PrivateKey.generate()
    raw = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()).hex()
    KEYFILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(KEYFILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)  # private from the moment it exists
    with os.fdopen(fd, 'w') as f: f.write(raw + '\n')
    print('PUBLIC KEY (safe to share; paste it on app.strikefinance.org/api-keys):')
    print(pubkey())
    print(f'\nPrivate key saved to {KEYFILE} (never share it or paste it anywhere).')


def _load():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if not KEYFILE.exists(): sys.exit('No API key yet. Ask Claude to "set me up", or run: python3 scripts/strike_api.py keygen')
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(KEYFILE.read_text().strip()[:64]))


def pubkey():
    from cryptography.hazmat.primitives import serialization
    return _load().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def request(method, path, body=None):
    priv = _load()
    body_str = json.dumps(body, separators=(',', ':')) if body is not None else ''
    ts = str(int(time.time())); nonce = str(uuid.uuid4())
    msg = f'{method.upper()}:{path}:{ts}:{nonce}:{hashlib.sha256(body_str.encode()).hexdigest()}'
    # Header name: the spec says X-Api-Wallet-Signature-Timestamp, but the server only accepts X-API-Wallet-Timestamp.
    cmd = ['curl', '-s', '--max-time', '30', '-X', method.upper(), STRIKE_API + path,
           '-H', f'X-API-Wallet-Public-Key: {pubkey()}', '-H', f'X-API-Wallet-Signature: {priv.sign(msg.encode()).hex()}',
           '-H', f'X-API-Wallet-Timestamp: {ts}', '-H', f'X-API-Wallet-Nonce: {nonce}', '-H', 'Content-Type: application/json']
    if body is not None: cmd += ['-d', body_str]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout
    try: return json.loads(out)
    except ValueError: return out


def _opts(args):
    kv = dict(a.split('=', 1) for a in args if '=' in a)
    return kv, [a for a in args if '=' not in a and a != '--yes'], '--yes' in args


def _send(method, path, body, yes):
    print(f'{method} {path}' + (f'\n{json.dumps(body, indent=1)}' if body is not None else ''))
    if not yes:
        print('\nDRY RUN: nothing sent. Add --yes to send it.'); return None
    r = request(method, path, body); print(json.dumps(r, indent=1)); return r


def _symbols(arg, sub):
    if arg in (None, 'all'): return sorted(markets())
    return [s if s.endswith('-USD') else s + '-USD' for s in arg.upper().split(',') if s]


def main(a):
    if not a: sys.exit(__doc__)
    cmd, rest = a[0], a[1:]
    kv, pos, yes = _opts(rest); sub = kv.get('sub')
    if cmd == 'keygen': keygen()
    elif cmd == 'pubkey': print(pubkey())
    elif cmd == 'get': print(json.dumps(request('GET', pos[0]), indent=1)[:20000])
    elif cmd == 'whoami':
        r = request('GET', '/v2/account')
        if not isinstance(r, dict) or 'account_id' not in r:
            sys.exit(f'The key was refused: {r}\nCheck the public key is added on app.strikefinance.org/api-keys and has not expired.')
        print(json.dumps({'account_id': r['account_id'], 'nickname': r.get('nickname'), 'wallet_balance': r.get('wallet_balance'),
                          'available_balance': r.get('available_balance'), 'default_leverage': r.get('default_leverage'),
                          'default_margin_mode': r.get('default_margin_mode'),
                          'sub_accounts': [{'id': s.get('account_id'), 'name': s.get('name')} for s in r.get('sub_accounts') or []]}, indent=1))
    elif cmd in ('set-leverage', 'set-margin-mode'):
        val = pos[0]; syms = _symbols(pos[1] if len(pos) > 1 else None, sub)
        for s in syms:
            body = {'symbol': s, 'leverage': int(val)} if cmd == 'set-leverage' else {'symbol': s, 'marginMode': val}
            if sub: body['sub_account_id'] = sub
            _send('POST', '/v2/leverage' if cmd == 'set-leverage' else '/v2/marginMode', body, yes)
            if yes: time.sleep(0.2)
    elif cmd == 'subscribe':
        lead = pos[0]
        body = {'lead_account_id': lead.lower() if lead.startswith('0x') else lead,
                'platform': kv.get('platform', 'hyperliquid' if lead.startswith('0x') else 'strike'), 'copy_existing_positions': False}
        if 'margin' in kv: body.update(copy_mode='fixed_amount', margin_per_entry_order=str(kv['margin']))
        elif 'ratio' in kv: body.update(copy_mode='fixed_ratio', copy_amount=str(kv['ratio']))
        else: sys.exit('say margin=<usd per entry> (fixed margin) or ratio=<usd allocation> (fixed ratio)')
        if kv.get('cap'): body['max_margin_per_symbol'] = str(kv['cap'])
        if kv.get('exclude'): body['excluded_symbols'] = [x for x in kv['exclude'].upper().split(',') if x]
        if sub: body['sub_account_id'] = sub
        if body['copy_mode'] == 'fixed_ratio':
            # Strike reserves a fixed-ratio copy amount from the available balance; a bigger amount is refused ("invalid inputs").
            acct = request('GET', '/v2/account' + (f'?sub_account_id={sub}' if sub else ''))
            avail = float(acct.get('available_balance') or 0) if isinstance(acct, dict) else 0.0
            if float(kv['ratio']) > avail:
                sys.exit(f"Not sent: a fixed-ratio copy amount is reserved from your available balance, and ${float(kv['ratio']):,.2f} is more than "
                         f"the ${avail:,.2f} available. Fund the account first, or use a copy amount up to ${avail:,.2f}.")
        r = _send('POST', '/v2/copy/subscribe', body, yes)
        if isinstance(r, dict) and r.get('subscription_id'):
            print(f"\nSubscribed: {r['subscription_id']}. The total cap is not part of subscribing; set it now with:\n"
                  f"  python3 scripts/strike_api.py caps {r['subscription_id']} <per_symbol> <total>" + (f' sub={sub}' if sub else ''))
        elif yes:
            print('\nStrike refused it, so no subscription was created (nothing to stop or cap).')
    elif cmd == 'caps':
        # max_margin_total is not in the spec; the field name comes from Strike's app, which sends both caps on every edit.
        body = {'max_margin_per_symbol': str(pos[1]), 'max_margin_total': str(pos[2])}
        if 'exclude' in kv: body['excluded_symbols'] = [x for x in kv['exclude'].upper().split(',') if x]
        if sub: body['sub_account_id'] = sub
        _send('PATCH', f'/v2/copy/subscribe/{pos[0]}', body, yes)
    elif cmd == 'stop':
        if len(pos) < 2 or pos[1] not in ('close', 'keep'): sys.exit('say "close" (close copied positions at market) or "keep" (leave them open as manual positions)')
        q = f"?cancel_open_orders=true&close_open_positions={'true' if pos[1] == 'close' else 'false'}" + (f'&sub_account_id={sub}' if sub else '')
        _send('DELETE', f'/v2/copy/subscribe/{pos[0]}{q}', None, yes)
    else:
        sys.exit(f'Unknown command "{cmd}".\n{__doc__}')


if __name__ == '__main__':
    main(sys.argv[1:])
