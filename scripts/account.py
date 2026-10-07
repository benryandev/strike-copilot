"""Regular review of your copy account: what you're copying, how it's doing, and anything that needs a decision.

  python3 scripts/account.py

Checks: balance and drawdown from peak (vs your max_dd), each subscription (mode, size, caps, P&L), each lead
(last trade, last 7 days on Strike markets, copiers, liquidations of the lead), copies the lead no longer holds,
liquidations of your copies in the last 7 days, markets whose
leverage or margin mode drifted from your profile, your fee, and the referral discount. Read-only.
"""
import sys, time, datetime, collections
from common import F, DAY, get, hl, now_ms, day, markets, hl_map, load_profile, fee_rate, COPIER_CAP
from strike_api import request


def main():
    p = load_profile(); NOW = now_ms(); sub = p.get('sub_account_id'); q = f'?sub_account_id={sub}' if sub else ''
    acct = request('GET', '/v2/account' + q)
    if not isinstance(acct, dict) or 'account_id' not in acct: sys.exit(f'The API key was refused: {acct}\nRenew it on app.strikefinance.org/api-keys (keys expire).')
    aid = acct['account_id']; flags = []
    print(f"== Account {'sub-account ' + sub if sub else 'main'} ({aid}) ==")
    bal, avail = F(acct.get('margin_balance') or acct.get('wallet_balance') or 0), F(acct.get('available_balance') or 0)
    pf = get(f'/v2/portfolio?account_id={aid}')
    h = [(r[0], F(r[1]), F(r[2]) + F(r[3])) for r in pf.get('history_perp_only') or [] if F(r[1]) or F(r[2])]
    pk = dd = 0.0; eq_pk = 0.0
    for _, eq, tp in h: pk = max(pk, tp); eq_pk = max(eq_pk, eq); dd = tp - pk
    dd_pct = dd / eq_pk if eq_pk else 0
    print(f"  balance ${bal:,.2f} (available ${avail:,.2f}); drop from peak P&L since the account opened {dd:+,.0f} ({dd_pct * 100:.0f}% of peak equity), your limit {F(p['max_dd']) * 100:.0f}%")
    if -dd_pct > F(p['max_dd']): flags.append(f"Drawdown {dd_pct * 100:.0f}% is past your {F(p['max_dd']) * 100:.0f}% limit: review the lead(s) now.")
    f, parts = fee_rate(p['account_id'] or aid)
    print(f"  fee {f * 100:.4f}%/side (tier {parts['tier']}, staking -{parts['staking'] * 100:.0f}%, referral -{parts['referral'] * 100:.0f}%)")

    print('\n== Subscriptions ==')
    subs = request('GET', '/v2/copy/subscriptions' + q).get('subscriptions') or []
    lb = get('/v2/copy/leaderboard?status=active&limit=500').get('entries') or []
    nc = collections.Counter(e['lead_account_id'].lower() for e in lb)
    if not subs: print('  none')
    for e in subs:
        amt = f"${F(e['copy_amount']):,.0f} ratio" if e['copy_mode'] == 'fixed_ratio' else f"${F(e.get('margin_per_entry_order') or 0):,.0f}/entry"
        caps = f"caps ${F(e.get('max_margin_per_symbol') or 0):,.0f}/symbol ${F(e.get('max_margin_total') or 0):,.0f} total" + (f", excluded {','.join(e['excluded_symbols'])}" if e.get('excluded_symbols') else '')
        print(f"  {e['subscription_id']}  {e['lead_account_id']}  {e['copy_mode']} {amt}  {e['status']}  since {day(e['created_at_ms'])}  {caps}")
        print(f"    margin in use ${F(e['total_margin']):,.0f}, P&L {F(e['pnl']):+,.2f} (realised {F(e['realized_pnl']):+,.2f}), {e.get('filled_order_count', 0)} copies filled, "
              f"lead has {nc.get(e['lead_account_id'].lower(), 0)}/{COPIER_CAP} copiers")
        lead_check(e['lead_account_id'], NOW, flags)

    print('\n== Your positions ==')
    pos = request('GET', '/v2/positions' + q).get('positions') or [] or []
    for x in pos:
        side = 1 if F(x['size']) > 0 else -1; mark = F(x.get('mark_price') or x['entry_price']); liq = F(x.get('liquidation_price') or 0)
        dist = (liq - mark) / mark * 100 * -side if liq else 0
        print(f"  {x['symbol']:12} {'LONG ' if side > 0 else 'SHORT'} ${abs(F(x['size'])) * mark:>9,.0f}  {x['margin_mode']} {x['leverage']}x  uPnL {F(x['upnl']):+,.2f}" + (f"  liquidation {dist:.1f}% away" if liq else ''))
    if not pos: print('  none')
    fills = get(f'/v2/history/fill?account_id={aid}&limit=1000').get('fills') or []
    orphan_check(pos, subs, flags)
    liqs = [x for x in fills if x.get('auto_close_type') and x['timestamp'] > NOW - 7 * DAY]
    if liqs: flags.append(f"{len(liqs)} of your copies were force-closed ({', '.join(sorted({x['symbol'] + ' ' + x['auto_close_type'] for x in liqs}))}) in the last 7 days.")

    mm = p.get('margin_mode') or 'isolated'
    print('\n== Leverage / margin mode (target: ' + f"{F(p['leverage']):.0f}x {mm}) ==")
    drift = [(s, st) for s, st in sorted((acct.get('symbol_settings') or {}).items()) if int(st['leverage']) != int(F(p['leverage'])) or st['margin_mode'] != mm]
    open_syms = {x['symbol'] for x in pos}
    for s, st in drift:
        print(f"  {s}: {st['margin_mode']} {st['leverage']}x" + (' (locked by an open position; fix when flat)' if s in open_syms else ' -> fix: strike_api.py set-leverage / set-margin-mode'))
    if not drift: print('  all markets match')

    ref = F(pf.get('fee_discount_rate') or 0)
    if not ref: print('\n(No referral fee discount on this account. Run python3 scripts/referral.py to see the option.)')
    print('\n== Needs attention ==')
    for x in flags: print('  !!', x)
    if not flags: print('  nothing: no change needed this week.')


def lead_check(lead, NOW, flags):
    try:
        if lead.lower().startswith('0x'):
            to_sym = hl_map()
            fl = hl({'type': 'userFillsByTime', 'user': lead.lower(), 'startTime': NOW - 30 * DAY, 'aggregateByTime': True}, list)
            perp = [x for x in fl if not x['coin'].startswith('@')]; last = max((x['time'] for x in perp), default=0)
            wk = [x for x in perp if x['time'] > NOW - 7 * DAY]
            on = sum(F(x['closedPnl']) for x in wk if x['coin'] in to_sym); off = sum(F(x['closedPnl']) for x in wk if x['coin'] not in to_sym)
            share = sum(1 for x in wk if x['coin'] in to_sym) / len(wk) if wk else 0
            # The lead's own liquidations/ADL on Strike markets. Strike doesn't copy these, so your copy stays open.
            # HL tags liquidation fills with liquidation.liquidatedUser (also present when the lead was only the other side).
            forced = [x for x in wk if x['coin'] in to_sym and ((x.get('liquidation') or {}).get('liquidatedUser', '').lower() == lead.lower()
                      or not x['dir'].startswith(('Open', 'Close', 'Long >', 'Short >', 'Buy', 'Sell', 'Spot')))]
            if forced:
                flags.append(f"Lead {lead[:10]} was force-closed on Hyperliquid this week ({', '.join(sorted({to_sym[x['coin']] for x in forced}))}). "
                             "Strike doesn't copy forced closes, so your copy of those markets may still be open, or bigger than the lead's "
                             "remaining position. Check \"Your positions\" above.")
        else:
            fl = get(f'/v2/history/fill?account_id={lead}&limit=1000').get('fills') or []
            last = max((x['timestamp'] for x in fl), default=0); wk = [x for x in fl if x['timestamp'] > NOW - 7 * DAY]
            on = sum(F(x['realized_pnl']) - F(x['fee']) for x in wk); off = 0.0; share = 1.0 if wk else 0
        idle = (NOW - last) / DAY if last else 99
        print(f"    lead: last trade {day(last) if last else 'none in 30 days'} ({idle:.1f} days ago); last 7 days realised {on:+,.0f} on Strike markets"
              + (f", {off:+,.0f} elsewhere; {share * 100:.0f}% of their fills on Strike markets" if lead.startswith('0x') else ''))
        if idle > 7: flags.append(f"Lead {lead[:10]} hasn't traded for {idle:.0f} days: idle can be normal, but check their history before deciding.")
        if on < 0 < off: flags.append(f"Lead {lead[:10]} made money this week only on markets Strike doesn't list ({off:+,.0f}) while the copyable side lost ({on:+,.0f}). "
                                  "One week is noise; two or three in a row is a reason to stop.")
    except Exception as ex:
        print(f'    lead check failed: {ex}')


def orphan_check(pos, subs, flags):
    """Copies the lead no longer holds. Strike doesn't copy a lead's liquidation (Strike, Oct 2026: the copy "just stays there"),
    so a copy can outlive the lead's position and nothing will close it. Compares your open positions with what your active
    Hyperliquid leads hold now. Strike's per-subscription position split (/v2/copy/positions) isn't open to API keys yet."""
    act = [e['lead_account_id'].lower() for e in subs if e['status'] == 'active']
    leads = [a for a in act if a.startswith('0x')]
    if not pos or not leads: return
    if len(leads) < len(act):
        print('\n(Orphan check skipped: you also copy a Strike trader, and this repo can\'t read their positions.)'); return
    to_sym = hl_map(); held = collections.defaultdict(set)
    try:
        for lead in leads:
            for dex in ('', 'xyz'):
                for a in hl({'type': 'clearinghouseState', 'user': lead, **({'dex': dex} if dex else {})}, dict).get('assetPositions') or []:
                    q = a['position']
                    if q['coin'] in to_sym: held[to_sym[q['coin']]].add(1 if F(q['szi']) > 0 else -1)
    except Exception as ex:
        print(f'\n(Orphan check failed: {ex})'); return
    for x in pos:
        side = 1 if F(x['size']) > 0 else -1
        if side in held.get(x['symbol'], ()): continue
        many = len(leads) > 1
        flags.append(f"{x['symbol']} {'long' if side > 0 else 'short'}: your lead{'s' if many else ''} "
                     f"{('now hold' if many else 'now holds') + ' the opposite side' if x['symbol'] in held else ('no longer hold' if many else 'no longer holds') + ' this market'}. If it's a copy, nothing will close it "
                     "for you (Strike doesn't copy a lead's liquidation). Run the review again in a few minutes in case the lead has just "
                     "traded; if it's still there, decide whether to close it yourself in the app or keep it.")


if __name__ == '__main__':
    main()
