"""Check one trader before copying them: the same checks as the sweep, shown one by one. ~30 seconds.

  python3 scripts/lead.py <0x Hyperliquid address | Strike account id>

Then run compare.py on them for fixed margin vs fixed ratio sized to your profile.
"""
import sys
from common import F, DAY, get, hl, now_ms, day, markets, hl_map, load_profile, my_fee, COPIER_CAP
from replay import hl_fills, hl_to_orders, strike_fills, strike_to_orders
import sweep as S


def main(lead):
    mk = markets(); fee = my_fee(); p = load_profile(required=False); NOW = now_ms(); checks = []
    is_hl = lead.lower().startswith('0x')
    sd = get(f'/v2/copy/discover/{lead.lower() if is_hl else lead}')
    if sd.get('address'):
        print(f"Strike profile: {sd.get('nickname') or ''} style {sd.get('style')}, account ${F(sd.get('account_value') or 0):,.0f}, copy score {sd.get('copy_score')}, "
              f"30d P&L {F((sd.get('pnl') or {}).get('30d') or 0):+,.0f} (Strike's figures include spot; checked below on fills)")
    else:
        print('Strike profile: none yet (new to Strike\'s list, or still being scored). It can still be copied.')
    if is_hl:
        a = lead.lower(); pf = dict(hl({'type': 'portfolio', 'user': a}, list)); st = hl({'type': 'clearinghouseState', 'user': a}, dict)
        d = lambda k: (lambda x: x[-1] - x[0] if x else 0)([F(v[1]) for v in pf[k]['pnlHistory']])
        h = pf['allTime']['accountValueHistory']; age = (NOW - h[0][0]) / DAY if h else 0
        perpM = d('perpMonth') / d('month') if d('month') else 0
        checks += [('Account at least 180 days old', age >= 180, f'{age:.0f} days'),
                   ('Never wiped out', S.wipes(h) == 0, f"{S.wipes(h)} wipe-outs"),
                   ('Profit comes from perps, not spot (this month)', perpM >= 0.7, f'{perpM * 100:.0f}% perps')]
        fills = hl_fills(a, S.T90, NOW); to_sym = hl_map(mk)
        tot = sum(F(x['closedPnl']) for x in fills if not x['coin'].startswith('@')); lst = sum(F(x['closedPnl']) for x in fills if x['coin'] in to_sym)
        share = lst / tot if tot > 0 else 0; orders = hl_to_orders(fills, mk)
        ms = st['marginSummary']; av = F(ms['accountValue'])
        live = f"perp account ${av:,.0f}, {F(ms['totalNtlPos']) / av if av else 0:.1f}x leverage overall, {len(st['assetPositions'])} positions"
    else:
        fills = strike_fills(lead, since=S.T90, pages=30); orders = strike_to_orders(fills, S.T90, NOW, mk); share = 1.0
        if fills and fills[0]['timestamp'] > S.T90 + DAY:
            print(f"Very active account: only the last {(NOW - fills[0]['timestamp']) / DAY:.1f} days of fills were read (30,000 fills). "
                  "Accounts trading this often are usually bots whose edge is too thin to copy.")
        own = [f for f in fills if not (f.get('client_order_id') or '').startswith('copy:')]
        h = [(r[0], r[1]) for r in get(f'/v2/portfolio?account_id={lead}').get('history_perp_only', []) if F(r[1])]
        age = (NOW - h[0][0]) / DAY if h else 0
        checks += [('On Strike for at least 14 days', age >= 14, f'{age:.0f} days'), ('Never wiped out', S.wipes(h) == 0, f'{S.wipes(h)} wipe-outs'),
                   ('Trades their own ideas (not mostly copying others)', len(own) >= 0.5 * len(fills) if fills else False, f'{len(own)} of {len(fills)} fills their own')]
        live = f"{len(get(f'/v2/positions?account_id={lead}').get('positions') or [])} open positions"
    m = S.metrics(orders, fee); budget = F(p.get('balance') or 1000) * F(p['max_use']); fit = budget / max(m['sim90']['entries'], 1)
    checks += [('>=70% of realised profit on markets Strike lists', share >= 0.7, f'{share * 100:.0f}%'),
               ('At least 20 round trips in 90 days', m['sim90']['trips'] >= 20, f"{m['sim90']['trips']} trips, {m['sim90']['win'] * 100:.0f}% winners"),
               ('Edge survives copy costs (>=20 bp per $)', m['edge_bp'] >= 20, f"{m['edge_bp']:.0f} bp"),
               ('Copy profitable over 90 days', m['sim90']['net'] > 0, f"{m['sim90']['net']:+,.0f} at $200 per entry, max drop {m['sim90']['mdd']:+,.0f}"),
               ('Copy profitable over 30 days', m['sim30']['net'] > 0, f"{m['sim30']['net']:+,.0f}, max drop {m['sim30']['mdd']:+,.0f}"),
               ('Fits your budget (>= $5 per entry)', fit >= 5, f"up to {m['sim90']['entries']} entries open at once -> ${fit:,.0f} per entry from ${budget:,.0f}")]
    nc = sum(1 for e in get('/v2/copy/leaderboard?status=active&limit=500').get('entries', []) if e['lead_account_id'].lower() == lead.lower())
    checks.append((f'Copier slots free ({COPIER_CAP} max)', nc < COPIER_CAP, f'{nc} copying now'))
    print(f"Period read: {m['orders']} orders on Strike markets ({m['opd']:.1f}/day): {', '.join(x[:-4] for x in m['markets'])}. Now: {live}.\n")
    for name, ok, detail in checks: print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    fails = [c for c in checks if not c[1]]
    print(f"\nPasses every check. Next: python3 scripts/compare.py {lead}" if not fails else
          f"\n{len(fails)} check(s) failed: not recommended for copying. (compare.py still works if you want to see the numbers.)")


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__)
    main(sys.argv[1])
