"""Strike API-wallet helper (Ed25519). Your private key lives in .secrets/strike_api.key on this computer only.

  python3 scripts/strike_api.py keygen        create a key; prints the PUBLIC key to paste on app.strikefinance.org/api-keys
  python3 scripts/strike_api.py pubkey        print the public key again
  python3 scripts/strike_api.py whoami        check the key works: account id, balance, sub-accounts
  python3 scripts/strike_api.py get <path>    any authenticated read, e.g. get "/v2/positions?sub_account_id=..."

Changes (each prints the exact request and does nothing unless you add --yes):
  set-leverage <10> [SYM-USD,...|all] [sub=<id>]           leverage for FUTURE positions (a market with an open position keeps its old setting)
  set-margin-mode <isolated|cross> [SYM-USD,...|all] [sub=<id>]
  add-margin <SYM-USD> <usd> [sub=<id>]                    add margin to an open isolated position (moves its liquidation price away; add only)
  subscribe <lead> margin=<usd>|ratio=<usd> [platform=hyperliquid|strike] [cap=<usd/symbol>] [tcap=<usd total>] [exclude=A-USD,B-USD]
            [min_entry=<usd>] [mult=<1-10>] [stop=<usd>] [sub=<id>]      (min_entry and mult: fixed ratio only)
  caps <subscription_id> <per_symbol_usd> <total_usd> [exclude=A-USD,...] [sub=<id>]     ("0" clears a cap)
  stop-loss <subscription_id> <usd> [sub=<id>]             end the copy once its P&L reaches -<usd> ("0" clears it)
  stop <subscription_id> [sub=<id>]                        stop copying; Strike closes the copied positions at market

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
    # Header name: older specs said X-Api-Wallet-Signature-Timestamp; the server (and spec v1.2.0) use X-API-Wallet-Timestamp.
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
    elif cmd == 'get':  # the path itself may contain '=' (a query string), so take it as given
        if not rest: sys.exit('say the path, e.g. get /v2/positions')
        print(json.dumps(request('GET', rest[0]), indent=1)[:20000])
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
    elif cmd == 'add-margin':
        # Add only: removing margin brings liquidation closer, so it isn't wired.
        if len(pos) < 2: sys.exit('say the market and the dollars to add, e.g. add-margin BTC-USD 50')
        sym = pos[0].upper() if pos[0].upper().endswith('-USD') else pos[0].upper() + '-USD'
        try: amt = float(pos[1])
        except ValueError: sys.exit('the amount is in dollars, e.g. add-margin BTC-USD 50')
        if amt <= 0: sys.exit('the amount to add must be more than 0')
        q = f'?sub_account_id={sub}' if sub else ''
        r = request('GET', '/v2/positions' + q)
        x = next((p for p in (r.get('positions') if isinstance(r, dict) else None) or [] if p.get('symbol') == sym), None)
        if not x or not float(x.get('size') or 0):
            sys.exit(f'No open {sym} position on this account (add sub=<id> for a sub-account).')
        if x.get('margin_mode') != 'isolated':
            sys.exit(f'Not sent: the {sym} position is cross margin, which already uses your whole balance. Margin can only be added to isolated positions.')
        acct = request('GET', '/v2/account' + q)
        avail = float(acct.get('available_balance') or 0) if isinstance(acct, dict) else 0.0
        if amt > avail:
            sys.exit(f'Not sent: ${amt:,.2f} is more than your available balance (${avail:,.2f}).')
        size, liq, entry = float(x['size']), float(x.get('liquidation_price') or 0), float(x['entry_price'])
        mark = entry + float(x.get('upnl') or 0) / size  # /v2/positions has no mark price
        # Isolated: maintenance margin depends on size and mark, not on the margin held, so each extra dollar
        # moves liquidation by 1/size in price. An estimate: Strike recalculates it on the next mark.
        new_liq = liq - amt / size
        far = lambda l: abs(mark - l) / mark * 100
        px = lambda v: f'{v:,.2f}' if v >= 100 else f'{v:.4g}'
        print(f"{sym} {'long' if size > 0 else 'short'} {abs(size):g}: margin ${float(x.get('iso_balance') or 0):,.2f}, mark about {px(mark)}, "
              f"liquidation {px(liq)} ({far(liq):.1f}% away).\n"
              f"Adding ${amt:,.2f} moves liquidation to about {px(new_liq)} ({far(new_liq):.1f}% away). "
              f"Available balance after: ${avail - amt:,.2f}.\n")
        body = {'symbol': sym, 'modify_type': 'add', 'amount': str(pos[1])}
        if sub: body['sub_account_id'] = sub
        _send('POST', '/v2/isoMargin', body, yes)
    elif cmd == 'subscribe':
        lead = pos[0]
        body = {'lead_account_id': lead.lower() if lead.startswith('0x') else lead,
                'platform': kv.get('platform', 'hyperliquid' if lead.startswith('0x') else 'strike'), 'copy_existing_positions': False}
        if 'margin' in kv: body.update(copy_mode='fixed_amount', margin_per_entry_order=str(kv['margin']))
        elif 'ratio' in kv: body.update(copy_mode='fixed_ratio', copy_amount=str(kv['ratio']))
        else: sys.exit('say margin=<usd per entry> (fixed margin) or ratio=<usd allocation> (fixed ratio)')
        # Field names as in Strike's copy-trading spec (v1.2.0, 13 Oct 2026).
        if kv.get('cap'): body['max_margin_per_symbol'] = str(kv['cap'])
        if kv.get('tcap'): body['max_margin_total'] = str(kv['tcap'])
        if kv.get('exclude'): body['excluded_symbols'] = [x for x in kv['exclude'].upper().split(',') if x]
        if body['copy_mode'] == 'fixed_ratio':
            if kv.get('min_entry'): body['minimum_entry'] = str(kv['min_entry'])  # small copies are raised to this trade value
            if kv.get('mult'):
                if not 1 <= float(kv['mult']) <= 10: sys.exit('mult (ratio multiplier) is 1 to 10')
                body['ratio_multiplier'] = str(kv['mult'])
        elif kv.get('min_entry') or kv.get('mult'): sys.exit('min_entry and mult only apply to fixed ratio')
        if kv.get('stop'):
            if float(kv['stop']) <= 0: sys.exit('stop is a positive dollar loss, e.g. stop=300')
            body['stop_loss_amount'] = str(kv['stop'])  # the copy ends once its P&L (after fees, incl. open) reaches minus this
        if sub: body['sub_account_id'] = sub
        # Strike refuses a copy larger than the current balance ("invalid inputs"; confirmed 2026-10-05), in both modes
        # (the app shows "Insufficient available balance").
        acct = request('GET', '/v2/account' + (f'?sub_account_id={sub}' if sub else ''))
        avail = float(acct.get('available_balance') or 0) if isinstance(acct, dict) else 0.0
        need = float(kv.get('ratio') or kv.get('margin'))
        if need > avail:
            what = 'fixed-ratio copy amount' if 'ratio' in kv else 'margin per entry'
            sys.exit(f"Not sent: the {what} (${need:,.2f}) is more than your available balance (${avail:,.2f}). "
                     f"Fund the account first, or use an amount up to ${avail:,.2f}.")
        r = _send('POST', '/v2/copy/subscribe', body, yes)
        if isinstance(r, dict) and r.get('subscription_id'):
            print(f"\nSubscribed: {r['subscription_id']}." + ('' if kv.get('tcap') else
                  f" No total cap was set; add one with:\n  python3 scripts/strike_api.py caps {r['subscription_id']} <per_symbol> <total>" + (f' sub={sub}' if sub else '')))
        elif yes:
            print('\nStrike refused it, so no subscription was created (nothing to stop or cap).')
    elif cmd == 'caps':
        body = {'max_margin_per_symbol': str(pos[1]), 'max_margin_total': str(pos[2])}
        if 'exclude' in kv: body['excluded_symbols'] = [x for x in kv['exclude'].upper().split(',') if x]
        if sub: body['sub_account_id'] = sub
        _send('PATCH', f'/v2/copy/subscribe/{pos[0]}', body, yes)
    elif cmd == 'stop-loss':
        if len(pos) < 2: sys.exit('say the subscription id and the dollar loss, e.g. stop-loss <id> 300 ("0" clears it)')
        amt = float(pos[1])
        if amt < 0: sys.exit('the stop loss is a positive dollar loss ("0" clears it)')
        # Strike counts the stop from the copy's start and refuses a level the copy has already lost (400).
        e = next((x for x in request('GET', '/v2/copy/subscriptions' + (f'?sub_account_id={sub}' if sub else '')).get('subscriptions') or []
                  if x['subscription_id'] == pos[0]), None)
        if not e or e.get('status') != 'active': sys.exit('No active subscription with that id on this account (add sub=<id> for a sub-account).')
        pnl = float(e.get('pnl') or 0)
        if amt and pnl <= -amt:
            sys.exit(f'Not sent: this copy is already at {pnl:+,.2f}, so a ${amt:,.0f} stop would fire at once and Strike refuses it. '
                     f'Pick more than ${-pnl:,.0f}.')
        if amt: print(f'This copy is at {pnl:+,.2f} since it started. A ${amt:,.0f} stop ends it at {-amt:,.2f}, '
                      f'after another ${amt + pnl:,.0f} of losses from here.\n')
        body = {'stop_loss_amount': str(pos[1])}
        if sub: body['sub_account_id'] = sub
        _send('PATCH', f'/v2/copy/subscribe/{pos[0]}', body, yes)
    elif cmd == 'stop':
        if len(pos) > 1 and pos[1] == 'keep':
            sys.exit('Strike no longer keeps copied positions open on stop (spec v1.2.0: stop "always closes its copied open positions"). '
                     'To stop new copies but keep what is open, exclude every market instead: caps <id> <per_symbol> <total> exclude=...')
        # Spec v1.2.0 lists no options: stop always closes copied positions. cancel_open_orders isn't in the spec; it's sent
        # in case the server still honours it (the app's stop doesn't cancel resting copied orders).
        q = '?cancel_open_orders=true&close_open_positions=true' + (f'&sub_account_id={sub}' if sub else '')
        _send('DELETE', f'/v2/copy/subscribe/{pos[0]}{q}', None, yes)
    else:
        sys.exit(f'Unknown command "{cmd}".\n{__doc__}')


if __name__ == '__main__':
    main(sys.argv[1:])
