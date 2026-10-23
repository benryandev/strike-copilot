"""Risk check on your open positions: what a sudden move would do, which stock/index/commodity positions sit closer to
liquidation than past weekends have moved, and copies holding opposite sides of the same market.

  python3 scripts/risk.py           the account in your profile (main, or the sub-account you set)
  python3 scripts/risk.py --all     main account and every active sub-account together (catches opposite sides across accounts)

1. Price shock: every position moved 5%, 10% and 20% down, then up, at once. Shows the P&L change and which positions
   would pass their liquidation price. An isolated position that's liquidated loses what's left in it, not more.
   Real moves aren't uniform (stocks and crypto don't always move together), so read it as a rough guide.
2. Weekend gap: stock, index and commodity markets follow their real market, which closes for the weekend. For each one
   you hold, the biggest move against your side from Friday 21:00 UTC (US close) to Monday 14:30 UTC (an hour after the
   open), over the last 12 finished weekends, on Strike's mark price. Flagged when your liquidation is closer than that.
3. Opposite sides: two copies (or a copy and a position on another account) long and short the same market. The
   overlapping part earns nothing (its moves and funding cancel) while each copy's trades still pay fees.
Read-only.
"""
import sys, time, datetime, collections
from common import F, get, markets, load_profile
from strike_api import request

SHOCKS = (0.05, 0.10, 0.20)
WEEKENDS = 12
HOUR = 3_600_000


def accounts(all_accounts):
    """[(label, query string)] for the profile's account, or main plus every active sub-account."""
    if not all_accounts:
        sub = load_profile().get('sub_account_id')
        return [('sub-account ' + sub[:8] if sub else 'main', f'?sub_account_id={sub}' if sub else '')]
    r = request('GET', '/v2/sub-accounts')
    subs = [s for s in (r.get('sub_accounts') or [] if isinstance(r, dict) else []) if s.get('status') == 'active' and not s.get('archived')]
    return [('main', '')] + [(s.get('name') or s['account_id'][:8], f"?sub_account_id={s['account_id']}") for s in subs]


def positions(q):
    """Open positions with mark (from entry + uPnL; /v2/positions has no mark), signed size, and liquidation distance."""
    out = []
    for x in request('GET', '/v2/positions' + q).get('positions') or []:
        size = F(x['size'])
        if not size: continue
        mark = F(x['entry_price']) + F(x.get('upnl') or 0) / size
        liq = F(x.get('liquidation_price') or 0)
        side = 1 if size > 0 else -1
        out.append(dict(sym=x['symbol'], size=size, side=side, mark=mark, liq=liq, upnl=F(x.get('upnl') or 0),
                        margin=F(x.get('iso_balance') or 0), mode=x['margin_mode'], lev=x['leverage'],
                        dist=(mark - liq) / mark * side if liq else None))   # fraction the price can move against you
    return out


def shock_table(rows):
    print('\n== Price shock (every position moved at once) ==')
    if not rows: print('  no open positions'); return []
    flags, many = [], len({a for a, _ in rows}) > 1
    for d in (-1, 1):
        for s in SHOCKS:
            move = d * s; total = 0.0; gone = []
            for a, p in rows:
                adverse = -move * p['side']                      # how far it moves against this position
                if p['dist'] is not None and adverse >= p['dist']:
                    gone.append(p['sym'].replace('-USD', '') + (f" ({a})" if many else ''))
                    total += -(p['margin'] + p['upnl']) if p['mode'] == 'isolated' else p['size'] * p['mark'] * move
                else:
                    total += p['size'] * p['mark'] * move
            print(f"  {move * 100:+4.0f}%  P&L {total:+10,.0f}" + (f"   liquidated: {', '.join(gone)}" if gone else ''))
            if s == SHOCKS[0] and gone:
                flags.append(f"A {s * 100:.0f}% move {'down' if d < 0 else 'up'} would liquidate {', '.join(gone)}: that's an ordinary day.")
    return flags


def weekend_moves(sym, now):
    """Biggest move down and up (fractions) from Friday 21:00 UTC to Monday 14:30 UTC, each of the last WEEKENDS weekends."""
    t = datetime.datetime.fromtimestamp(now / 1000, datetime.UTC)
    fri = (t - datetime.timedelta(days=(t.weekday() - 4) % 7)).replace(hour=21, minute=0, second=0, microsecond=0)
    if fri > t: fri -= datetime.timedelta(days=7)
    t0 = int((fri - datetime.timedelta(weeks=WEEKENDS)).timestamp() * 1000)
    bars, cur = [], t0
    while cur < now:
        k = get(f'/price/v2/klines?symbol={sym}&interval=1h&priceType=mark&startTime={cur}&endTime={min(cur + 1000 * HOUR, now)}&limit=1000')
        if not isinstance(k, list) or not k: break
        bars += k; cur = k[-1][0] + HOUR
    by_t = {b[0]: b for b in bars}
    downs, ups = [], []
    for w in range(1, WEEKENDS + 1):
        start = int((fri - datetime.timedelta(weeks=w - 1)).timestamp() * 1000)
        if start + 65.5 * HOUR > now: continue                     # this weekend hasn't finished yet
        ref = by_t.get(start - HOUR)
        span = [by_t[t] for t in range(start, start + int(65.5 * HOUR), HOUR) if t in by_t]
        if not ref or not span: continue
        c = F(ref[4])
        downs.append(min(F(b[3]) for b in span) / c - 1); ups.append(max(F(b[2]) for b in span) / c - 1)
    return downs, ups


def weekend_check(rows, mk, now):
    print(f'\n== Weekend gap (stock, index and commodity positions; last {WEEKENDS} weekends) ==')
    held = [(a, p) for a, p in rows if (mk.get(p['sym'], {}).get('hl') or '').startswith('xyz:')]
    if not held: print('  none held'); return []
    flags, cache = [], {}
    for a, p in held:
        if p['sym'] not in cache: cache[p['sym']] = weekend_moves(p['sym'], now)
        downs, ups = cache[p['sym']]
        if not downs: print(f"  {p['sym']:12} no price history"); continue
        worst = -min(downs) if p['side'] > 0 else max(ups)        # biggest move against this side
        typical = sorted(-x if p['side'] > 0 else x for x in (downs if p['side'] > 0 else ups))[len(downs) // 2]
        dist = p['dist']
        print(f"  {p['sym']:12} {'long ' if p['side'] > 0 else 'short'} ({a})  liquidation {dist * 100:.1f}% away; against you over a weekend: "
              f"typical {typical * 100:.1f}%, worst {worst * 100:.1f}% ({len(downs)} weekends)" if dist is not None else
              f"  {p['sym']:12} ({a}) no liquidation price")
        if dist is not None and dist <= worst:
            flags.append(f"{p['sym']} {'long' if p['side'] > 0 else 'short'} ({a}) is {dist * 100:.1f}% from liquidation; the worst weekend in the last "
                         f"{len(downs)} moved {worst * 100:.1f}% against that side. Decide before the market closes for the weekend: add isolated "
                         "margin (strike_api.py add-margin), or accept that a weekend like that would close it.")
    return flags


def opposite_sides(rows, copies):
    """Markets held long and short at once: across accounts (positions), or by two copies inside one account (Strike nets them)."""
    print('\n== Opposite sides ==')
    flags = []
    by = collections.defaultdict(list)
    for a, p in rows: by[p['sym']].append((a, p['side'], abs(p['size']) * p['mark']))
    split = collections.defaultdict(set)
    for (a, sub, lead, sym, side) in copies: split[(a, sym)].add((lead, side))
    found = False
    for sym, xs in sorted(by.items()):
        if {s for _, s, _ in xs} == {1, -1}:
            found = True
            who = ', '.join(f"{'long' if s > 0 else 'short'} ${v:,.0f} on {a}" for a, s, v in sorted(xs))
            hedged = min(sum(v for _, s, v in xs if s > 0), sum(v for _, s, v in xs if s < 0))
            print(f"  {sym}: {who}")
            flags.append(f"{sym} is {who}: ${hedged:,.0f} of it offsets itself, so that part earns nothing while each copy's trades still pay fees. "
                         "Fine if you mean the two copies to run independently; otherwise one of them is working against the other.")
    for (a, sym), xs in sorted(split.items()):
        if {s for _, s in xs} == {1, -1}:
            found = True
            leads = ', '.join(f"{l[:10]} {'long' if s > 0 else 'short'}" for l, s in sorted(xs))
            print(f"  {sym} on {a}: copies pull opposite ways ({leads}); Strike nets them into one position")
            flags.append(f"{sym} on {a}: two copies hold opposite sides ({leads}). Each copy's trades still pay fees while the net barely moves.")
    if not found: print('  none')
    return flags


def main():
    all_accounts = '--all' in sys.argv
    now = int(time.time() * 1000); mk = markets()
    rows, copies, flags = [], set(), []
    for label, q in accounts(all_accounts):
        for p in positions(q): rows.append((label, p))
        cp = request('GET', '/v2/copy/positions' + q)
        sl = request('GET', '/v2/copy/subscriptions' + q)
        leads = {e['subscription_id']: e['lead_account_id'] for e in (sl.get('subscriptions') or [] if isinstance(sl, dict) else [])}
        for c in (cp.get('positions') or [] if isinstance(cp, dict) else []):
            copies.add((label, c['subscription_id'], leads.get(c['subscription_id'], c['subscription_id']), c['market'], 1 if c['side'] == 'long' else -1))
    print(f"== Open positions ({', '.join(sorted({a for a, _ in rows})) or 'none'}) ==")
    for a, p in sorted(rows, key=lambda r: r[1]['dist'] if r[1]['dist'] is not None else 9):
        print(f"  {p['sym']:12} {'long ' if p['side'] > 0 else 'short'} ${abs(p['size']) * p['mark']:>9,.0f}  {p['mode']} {p['lev']}x  margin ${p['margin']:,.0f}"
              + (f"  liquidation {p['dist'] * 100:.1f}% away" if p['dist'] is not None else '') + (f"  ({a})" if all_accounts else ''))
    flags += shock_table(rows)
    flags += weekend_check(rows, mk, now)
    flags += opposite_sides(rows, copies)
    print('\n== Needs attention ==')
    for x in flags: print('  !!', x)
    if not flags: print('  nothing from these three checks.')


if __name__ == '__main__':
    main()
