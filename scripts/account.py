"""Regular review of your copy account: what you're copying, how it's doing, and anything that needs a decision.

  python3 scripts/account.py

Checks: balance and drawdown from peak (vs your max_dd), each subscription (mode, size, caps, P&L), each lead
(last trade, last 7 days on Strike markets, copiers, liquidations of the lead), each subscription's open copies,
copies your caps skipped in the last 7 days (and roughly what they'd be worth now), copies the lead no longer holds,
liquidations of your copies in the last 7 days, your own copiers if others copy you, markets whose
leverage or margin mode drifted from your profile, your fee, and the referral discount. Read-only.
"""
import sys, time, datetime, collections
from common import F, DAY, get, hl, now_ms, day, markets, hl_map, load_profile, fee_rate, COPIER_CAP
from strike_api import request
from replay import hl_fills


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
    cp = request('GET', '/v2/copy/positions' + q)
    cpos = cp.get('positions') if isinstance(cp, dict) else None  # per-subscription split; None if the key can't read it
    if not subs: print('  none')
    for e in subs:
        amt = f"${F(e['copy_amount']):,.0f} ratio" if e['copy_mode'] == 'fixed_ratio' else f"${F(e.get('margin_per_entry_order') or 0):,.0f}/entry"
        caps = f"caps ${F(e.get('max_margin_per_symbol') or 0):,.0f}/symbol ${F(e.get('max_margin_total') or 0):,.0f} total" + (f", excluded {','.join(e['excluded_symbols'])}" if e.get('excluded_symbols') else '')
        print(f"  {e['subscription_id']}  {e['lead_account_id']}  {e['copy_mode']} {amt}  {e['status']}  since {day(e['created_at_ms'])}  {caps}")
        print(f"    margin in use ${F(e['total_margin']):,.0f}, worth ${F(e.get('total_worth') or 0):,.0f}; P&L {F(e['pnl']):+,.2f} "
              f"(realised {F(e['realized_pnl']):+,.2f}, open {F(e['pnl']) - F(e['realized_pnl']):+,.2f}), {e.get('filled_order_count', 0)} copies filled, "
              f"lead has {nc.get(e['lead_account_id'].lower(), 0)}/{COPIER_CAP} copiers")
        sl = F(e.get('stop_loss_amount') or 0)
        lev = ("the lead's leverage" + (f" (max {e['max_leverage']}x)" if e.get('max_leverage') else '')) if e.get('leverage_source') == 'lead' else 'your leverage'
        mm_src = "the lead's margin mode" if e.get('margin_mode_source') == 'lead' else 'your margin mode'
        if e['status'] == 'active':
            room = F(e['pnl']) + sl
            stop_txt = f"${sl:,.0f} (ends the copy at P&L {-sl:,.0f}; ${room:,.0f} away)" if sl else 'none'
            print(f"    stop loss {stop_txt}; copies use {lev} and {mm_src}")
            if sl and room < 0.25 * sl:
                flags.append(f"Copy of {e['lead_account_id'][:10]} is ${room:,.0f} from its ${sl:,.0f} stop loss: when it fires, Strike stops the copy and closes its positions at market.")
        for x in sorted((c for c in cpos or [] if c['subscription_id'] == e['subscription_id']), key=lambda c: liq_dist(c)):
            d = liq_dist(x)
            print(f"      {x['market']:12} {x['side']:5} margin ${F(x['collateral']):>7,.0f}  {x['margin_mode']} {x['leverage']}x  P&L {F(x['pnl']):+9,.2f}"
                  + (f"  liquidation {d:.1f}% away" if d < 999 else ''))
            if d < 3: flags.append(f"{x['market']} {x['side']} copy is {d:.1f}% from liquidation (margin ${F(x['collateral']):,.0f}). Decide now: add isolated margin, or let it go.")
        lead_check(e['lead_account_id'], NOW, flags)
    stopped_by_strike(q, NOW, flags)

    print('\n== Your positions ==')
    pos = request('GET', '/v2/positions' + q).get('positions') or [] or []
    for x in pos:
        side = 1 if F(x['size']) > 0 else -1; liq = F(x.get('liquidation_price') or 0)
        mark = F(x.get('mark_price') or 0) or F(x['entry_price']) + F(x.get('upnl') or 0) / F(x['size'])  # /v2/positions has no mark price
        dist = (liq - mark) / mark * 100 * -side if liq else 0
        print(f"  {x['symbol']:12} {'LONG ' if side > 0 else 'SHORT'} ${abs(F(x['size'])) * mark:>9,.0f}  {x['margin_mode']} {x['leverage']}x  uPnL {F(x['upnl']):+,.2f}" + (f"  liquidation {dist:.1f}% away" if liq else ''))
    if not pos: print('  none')
    fills = get(f'/v2/history/fill?account_id={aid}&limit=1000').get('fills') or []
    orphan_check(pos, subs, flags, cpos)
    skip_report(subs, q, acct, NOW, flags)
    my_copiers(q)
    liqs = [x for x in fills if x.get('auto_close_type') and x['timestamp'] > NOW - 7 * DAY]
    ev = {(x['symbol'], x['auto_close_type'], x['timestamp'] // 60000) for x in liqs}  # one liquidation can fill in several pieces
    if ev: flags.append(f"{len(ev)} of your positions were force-closed in the last 7 days ({', '.join(f'{s} {t}' for s, t in sorted(collections.Counter((s, t) for s, t, _ in ev)))}).")

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
            # userFillsByTime returns the OLDEST 2,000 fills from startTime, so one call made a busy lead look idle (16 Oct 2026).
            # Last trade from userFills (the newest 2,000); the week's P&L from a paged fetch.
            recent = hl({'type': 'userFills', 'user': lead.lower()}, list)
            last = max((x['time'] for x in recent if not x['coin'].startswith('@')), default=0)
            wk = [x for x in hl_fills(lead.lower(), NOW - 7 * DAY, NOW) if not x['coin'].startswith('@')]
            on = sum(F(x['closedPnl']) for x in wk if x['coin'] in to_sym); off = sum(F(x['closedPnl']) for x in wk if x['coin'] not in to_sym)
            share = sum(1 for x in wk if x['coin'] in to_sym) / len(wk) if wk else 0
            # The lead's own liquidations/ADL on Strike markets. Since 12 Oct 2026 Strike closes the copy by the same fraction.
            # HL tags liquidation fills with liquidation.liquidatedUser (also present when the lead was only the other side).
            forced = [x for x in wk if x['coin'] in to_sym and ((x.get('liquidation') or {}).get('liquidatedUser', '').lower() == lead.lower()
                      or not x['dir'].startswith(('Open', 'Close', 'Long >', 'Short >', 'Buy', 'Sell', 'Spot')))]
            if forced:
                flags.append(f"Lead {lead[:10]} was force-closed on Hyperliquid this week ({', '.join(sorted({to_sym[x['coin']] for x in forced}))}). "
                             "Strike now closes copies by the same fraction when that happens; check \"Your positions\" above that yours "
                             "closed too.")
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


def stopped_by_strike(q, NOW, flags):
    """Copies Strike itself stopped in the last 7 days (stop loss). The default subscription list only has active ones."""
    try:
        r = request('GET', '/v2/copy/subscriptions' + (q + '&' if q else '?') + 'status=stopped')
        rows = r.get('subscriptions') or [] if isinstance(r, dict) else []
    except Exception: return
    for e in rows:
        if e.get('stop_reason') == 'stop_loss' and (e.get('stopped_at_ms') or 0) > NOW - 7 * DAY:
            flags.append(f"Strike stopped your copy of {e['lead_account_id'][:10]} on {day(e['stopped_at_ms'])}: its "
                         f"${F(e.get('stop_loss_amount') or 0):,.0f} stop loss was reached and its positions were closed (final P&L "
                         f"{F(e.get('pnl') or 0):+,.2f}). Nothing copies from this lead now unless you subscribe again.")


def liq_dist(x):
    """% between mark and liquidation for a /v2/copy/positions row (999 if there's no liquidation price)."""
    mark, liq = F(x.get('mark_price') or 0), F(x.get('liquidation_price') or 0)
    return abs(liq - mark) / mark * 100 if mark and liq else 999


def lead_sides(lead, to_sym):
    """{Strike symbol: {+1/-1}} the lead holds now: Hyperliquid (crypto + xyz books) or a Strike account (public live positions)."""
    held = collections.defaultdict(set)
    if lead.lower().startswith('0x'):
        for dex in ('', 'xyz'):
            for a in hl({'type': 'clearinghouseState', 'user': lead.lower(), **({'dex': dex} if dex else {})}, dict).get('assetPositions') or []:
                q = a['position']
                if q['coin'] in to_sym: held[to_sym[q['coin']]].add(1 if F(q['szi']) > 0 else -1)
    else:
        for x in get('/stat/v1/stats/live-positions').get('positions') or []:
            if x.get('account_id') == lead and F(x['size']): held[x['symbol']].add(1 if F(x['size']) > 0 else -1)
    return held


def orphan_check(pos, subs, flags, cpos=None):
    """Copies the lead no longer holds. Since 12 Oct 2026 Strike closes a copy when the lead is liquidated, so this should
    be rare (a missed or failed close, or copy lag). Uses Strike's per-subscription split (/v2/copy/positions), so manual
    positions aren't mistaken for copies and Strike leads are checked too; falls back to comparing all your positions
    with your Hyperliquid leads if the key can't read that route."""
    act = {e['subscription_id']: e['lead_account_id'] for e in subs if e['status'] == 'active'}
    if not act: return
    to_sym = hl_map(); cache = {}
    def held(lead):
        if lead not in cache: cache[lead] = lead_sides(lead, to_sym)
        return cache[lead]
    try:
        if cpos is not None:
            rows = [(x['market'], 1 if x['side'] == 'long' else -1, act[x['subscription_id']]) for x in cpos if x['subscription_id'] in act]
        else:
            leads = [a for a in act.values() if a.startswith('0x')]
            if len(leads) < len(act): print('\n(Orphan check skipped: this API key can\'t read /v2/copy/positions, and Strike leads need it.)'); return
            merged = collections.defaultdict(set)
            for lead in leads:
                for k, v in held(lead).items(): merged[k] |= v
            rows = [(x['symbol'], 1 if F(x['size']) > 0 else -1, None) for x in pos]
            cache['*'] = merged
    except Exception as ex:
        print(f'\n(Orphan check failed: {ex})'); return
    for sym, side, lead in rows:
        h = cache['*'] if lead is None else held(lead)
        if side in h.get(sym, ()): continue
        who = f"lead {lead[:10]}" if lead else 'your leads'
        flags.append(f"{sym} {'long' if side > 0 else 'short'}: {who} {'now hold(s) the opposite side' if sym in h else 'no longer hold(s) this market'}. "
                     "If it's a copy, it may have missed a close. Run the review again in a few minutes in case the lead has just "
                     "traded; if it's still there, decide whether to close it yourself in the app or keep it.")


def copy_orders(q, since):
    """Your copy-order records (placed and failed) since `since` ms, newest first."""
    out, cur = [], ''
    for _ in range(20):
        r = request('GET', '/v2/copy/orders' + (q + '&' if q else '?') + 'limit=200' + cur)
        if not isinstance(r, dict) or 'orders' not in r: return None
        out += r['orders']
        if not r['orders'] or r['orders'][-1]['created_at_ms'] < since or not r.get('next_before_ms'): break
        cur = f"&before={r['next_before_ms']}&before_id={r['next_before_id']}"
    return [o for o in out if o['created_at_ms'] >= since]


REASONS = {'max_margin_total': 'total cap', 'max_margin_per_symbol': 'per-symbol cap'}


def skip_report(subs, q, acct, NOW, flags):
    """Copies your caps (or other checks) skipped in the last 7 days, per subscription. For fixed-margin copies of a
    Hyperliquid lead, also a rough value: what each skipped entry would be worth at today's price had it been copied at
    the lead's fill price (your margin per entry x your leverage, before fees, ignoring any later closes by the lead)."""
    orders = copy_orders(q, NOW - 7 * DAY)
    print('\n== Copies skipped in the last 7 days ==')
    if orders is None: print("  (this API key can't read /v2/copy/orders)"); return
    if not subs or not orders: print('  none'); return
    to_sym = hl_map(); mids = {}
    lev = {s: int(st['leverage']) for s, st in (acct.get('symbol_settings') or {}).items()}
    for e in subs:
        mine = [o for o in orders if o['subscription_id'] == e['subscription_id']]
        bad = [o for o in mine if o['status'] == 'failed']
        if not mine: continue
        why = collections.Counter(REASONS.get(o.get('error_message'), o.get('error_message') or 'unknown') for o in bad)
        print(f"  {e['lead_account_id'][:10]}: {len(mine) - len(bad)} copied, {len(bad)} skipped"
              + (f" ({', '.join(f'{n} {r}' for r, n in why.most_common())})" if bad else ''))
        if not bad: continue
        val = None; lead = e['lead_account_id'].lower()
        if lead.startswith('0x') and e['copy_mode'] == 'fixed_amount':
            try:
                fl = hl_fills(lead, NOW - 8 * DAY, NOW)
                first = {}
                for x in sorted(fl, key=lambda x: x['time']): first.setdefault(str(x['oid']), x)
                if not mids:
                    for dex in ('', 'xyz'): mids.update(hl({'type': 'allMids', **({'dex': dex} if dex else {})}, dict))
                m = F(e.get('margin_per_entry_order') or 0); val = 0.0; n = 0; bym = collections.defaultdict(float)
                for o in bad:
                    x = first.get(o['lead_client_order_id'].split(':')[-1])
                    if not x or x['coin'] not in mids: continue
                    side = 1 if x['side'] == 'B' else -1
                    v = m * lev.get(o['symbol'], 10) * side * (F(mids[x['coin']]) / F(x['px']) - 1)
                    val += v; bym[o['symbol']] += v; n += 1
                if n:
                    top = ', '.join(f"{s} {v:+,.0f}" for s, v in sorted(bym.items(), key=lambda kv: kv[1])[:4])
                    print(f"    if copied at the lead's price and held to now: about {val:+,.0f} on {n} of them ({top})")
            except Exception as ex:
                print(f'    (value estimate failed: {ex})')
        share = len(bad) / len(mine)
        if share >= 0.25 and any(o.get('error_message') in REASONS for o in bad):
            effect = '' if val is None else (f"; held to now they'd be about {val:+,.0f}, so the caps saved you about ${-val:,.0f} this week" if val < 0
                                             else f"; held to now they'd be about {val:+,.0f}, so the caps cost you about ${val:,.0f} this week")
            flags.append(f"Your caps skipped {len(bad)} of {len(mine)} copies from {e['lead_account_id'][:10]} this week ({share * 100:.0f}%){effect}. "
                         "If most copies keep getting skipped, the cap or the margin per entry is out of step with how often this lead trades. "
                         "One week isn't enough to change it; look again next review.")


def my_copiers(q):
    """If other people copy this account, their count, volume and the profit share you've earned (last 30 days)."""
    r = request('GET', '/v2/copy/lead/stats' + (q + '&' if q else '?') + 'days=30')
    if not isinstance(r, dict) or 'daily' not in r: return
    daily = r.get('daily') or []; t = r.get('today') or {}
    peak = max((int(d.get('copier_count') or 0) for d in daily), default=0)
    if not peak and not r.get('profit_shares'): return
    vol = sum(F(d.get('volume') or 0) for d in daily); shared = sum(F(d.get('shared_profit') or 0) for d in daily)
    print(f"\n== People copying you (last 30 days) ==\n  copiers now {t.get('copier_count', 0)} (most {peak}), their copied volume ${vol:,.0f}, "
          f"profit share earned ${shared:,.2f} at {F(r.get('profit_share_rate') or 0) * 100:.0f}% (pending ${F(t.get('unrealized_shared_profit') or 0):,.2f}). "
          "Changes to this account's trading or copies also change what they copy.")


if __name__ == '__main__':
    main()
