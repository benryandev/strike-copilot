"""Fixed margin vs fixed ratio for one or more leads, sized to your profile, with the settings to use.

  python3 scripts/compare.py <lead> [<lead> ...] [days=90]

For each lead it replays the last 90 and the last 30 days on your balance, with your fee, leverage, exclusions and a
total cap of balance x max_use (so neither mode can use more than you allowed), and picks a mode per window:
- If only one mode made money, it wins. If both lost, neither is recommended.
- Otherwise the mode with clearly more profit per $ of drawdown (net / biggest drop) wins. A smaller drop compounds
  better: a mode with half the drop and half the profit can be sized up to match.
- Within 10% on that measure, the smaller drawdown wins. That is usually fixed ratio; when it is, the result says so
  with a disclosure whenever the recommended mode trades more volume, since that helps the repo author's referral tier.
- A clear win in one window beats a tie in the other. If the windows clearly disagree, the smaller 90-day drawdown
  wins, and the result says so.
Results are saved to data/compare/<lead>.json.
"""
import sys, json, math
from common import F, DAY, DATA, get, load_profile, my_fee, markets, COPIER_CAP
from replay import Lead

TIE_REL, TIE_ABS = 0.10, 0.02


def copiers(lead):
    n = 0
    for st in ('active',):
        n += sum(1 for e in get(f'/v2/copy/leaderboard?status={st}&limit=500').get('entries', []) if e['lead_account_id'].lower() == lead.lower())
    return n


def close(a, b, acct):
    return abs(a - b) <= max(TIE_ABS * acct, TIE_REL * max(abs(a), abs(b)))


def score(r, acct):
    return r['net'] / max(-r['dd'], 0.01 * acct)  # profit per $ of drawdown (drop floored at 1% of the balance)


def verdict(fm, fr, acct):
    """-> (mode or None, reason). mode is 'fixed margin', 'fixed ratio' or None (neither)."""
    if fm['wiped'] and not fr['wiped']: return 'fixed ratio', 'fixed margin wiped the account out'
    if fr['wiped'] and not fm['wiped']: return 'fixed margin', 'fixed ratio wiped the account out'
    if fm['net'] <= 0 and fr['net'] <= 0: return None, 'both modes lost money'
    if fm['net'] <= 0: return 'fixed ratio', 'only fixed ratio made money'
    if fr['net'] <= 0: return 'fixed margin', 'only fixed margin made money'
    sm, sr = score(fm, acct), score(fr, acct)
    if abs(sm - sr) <= TIE_REL * max(sm, sr):
        return ('fixed ratio' if fr['dd'] >= fm['dd'] else 'fixed margin'), f'profit per $ of drawdown about even ({sm:.1f} vs {sr:.1f}): smaller drawdown wins'
    return ('fixed margin' if sm > sr else 'fixed ratio'), f'more profit per $ of drawdown ({max(sm, sr):.1f} vs {min(sm, sr):.1f})'


def combine(ws, acct):
    """Clear wins (better profit per $ of drawdown) outrank ties; only if clear wins conflict, or there are none and the
    ties disagree, does the smaller 90-day drawdown decide."""
    vs = {d: w['verdict'] for d, w in ws.items()}
    detail = '; '.join(f'{d}d: {v[1]}' for d, v in vs.items())
    if any(m is None for m, _ in vs.values()): return None, detail
    clear = {m for m, why in vs.values() if 'about even' not in why}
    if len(clear) == 1: return clear.pop(), detail
    if not clear and len({m for m, _ in vs.values()}) == 1: return next(iter(vs.values()))[0], detail
    w90 = ws[max(ws, key=int)]
    m = 'fixed ratio' if w90['fixed_ratio']['dd'] >= w90['fixed_margin']['dd'] else 'fixed margin'
    return m, f'the windows disagree ({detail}): smaller {max(ws, key=int)}-day drawdown wins'


def nice(x, step):
    return max(step, math.floor(x / step) * step)


def window(L, days):
    """A view of a 90-day Lead restricted to its last `days` days (same fetched data, so both windows agree on the lead)."""
    t0 = L.now - int(days * DAY)
    V = object.__new__(Lead); V.__dict__.update(L.__dict__)
    V.orders = [o for o in L.orders if o['t'] >= t0]; V.marks = [m for m in L.marks if m[0] >= t0]; V.t0 = t0
    return V


def analyse(lead, p, days=90, mk=None):
    acct = F(p['balance']); lev = F(p['leverage']); budget = acct * F(p['max_use']); ex = p['excluded']; fee = my_fee()
    L = Lead(lead, days, mk)
    if not L.orders: return {'lead': lead, 'error': f'no orders on Strike-listed markets in the last {days} days'}
    # Fixed margin size: as much per entry as fits the lead's peak number of open entries inside your budget
    probe = L.run(1e9, L.fm(10, lev), lev, fee=fee, excluded=ex)  # unconstrained: how many entries the lead stacks
    entries = max(probe['peak_entries'], 1)
    margin = nice(budget / entries, 5 if budget / entries >= 10 else 1)
    min_margin = max(mk_v['min_notional'] for s, mk_v in L.mk.items() if s in L.symbols) / lev
    sized = L.run(acct, L.fm(margin, lev), lev, fee=fee, excluded=ex)
    sym_cap = min(budget, math.ceil(max(sized['sym_pk'].values() or [margin]) / 50) * 50)
    out = {'lead': lead, 'account': acct, 'leverage': lev, 'budget': budget, 'fee': fee, 'excluded': ex,
           'markets': [m for m in L.symbols if m not in ex], 'unmarked': L.unmarked, 'peak_entries': entries, 'copiers': copiers(lead),
           'settings': {'fixed_margin': {'margin_per_entry': margin, 'max_concurrent_entries': entries},
                        'fixed_ratio': {'copy_amount': acct, 'ratio_now': acct / L.equity(L.now) if L.equity(L.now) else 0},
                        'caps': {'per_symbol': sym_cap, 'total': budget}},
           'too_small': margin < min_margin, 'max_dd': F(p['max_dd']), 'windows': {}}
    for d in (days, 30):
        V = window(L, d) if d != days else L
        fm = V.run(acct, V.fm(margin, lev), lev, fee=fee, cap=sym_cap, tcap=budget, excluded=ex)
        fr = V.run(acct, V.fr(acct), lev, fee=fee, cap=sym_cap, tcap=budget, excluded=ex)
        out['windows'][d] = {'fixed_margin': fm, 'fixed_ratio': fr, 'verdict': verdict(fm, fr, acct), 'orders': len(V.orders)}
    out['verdict'] = combine(out['windows'], acct)
    out['too_deep'] = [m for w in out['windows'].values() for m in ('fixed_margin', 'fixed_ratio') if -w[m]['dd_pct'] > F(p['max_dd'])]
    return out


def report(r):
    if 'error' in r: return f"\n== {r['lead']} ==\n  {r['error']}"
    s = r['settings']; a = r['account']; lines = [f"\n== {r['lead']} ==  active copiers {r['copiers']}/{COPIER_CAP}"
                                                    + ('  ** FULL: cannot be copied right now' if r['copiers'] >= COPIER_CAP else '')]
    lines.append(f"  markets: {', '.join(m[:-4] for m in r['markets'])}" + (f" (no Hyperliquid price, marked at fills: {', '.join(r['unmarked'])})" if r['unmarked'] else ''))
    lines.append(f"  {'window':8} {'mode':13} {'net':>9} {'lowest':>9} {'max drop':>14} {'margin used':>12} {'fees':>7} {'volume':>10}  skipped")
    for d, w in r['windows'].items():
        for m, lab in (('fixed_margin', f"FM ${s['fixed_margin']['margin_per_entry']:,.0f}"), ('fixed_ratio', f"FR ${s['fixed_ratio']['copy_amount']:,.0f}")):
            x = w[m]
            lines.append(f"  {str(d) + 'd':8} {lab:13} {x['net']:>+9,.0f} {x['low']:>9,.0f} {x['dd']:>+8,.0f} ({x['dd_pct'] * 100:>3.0f}%) {x['peak_margin']:>12,.0f} {x['fees']:>7,.0f} {x['volume']:>10,.0f}  "
                         + (', '.join(f'{v} {k}' for k, v in x['skipped'].items()) or '-') + (f'  WIPED {x["wiped"]}' if x['wiped'] else ''))
        lines.append(f"  {'':8} -> {w['verdict'][0] or 'neither'}: {w['verdict'][1]}")
    lines.append('  (margin used = peak margin at market prices; caps are checked on margin at entry, so it can sit a little above the total cap)')
    m, why = r['verdict']
    if m is None: lines.append(f"  VERDICT: NEITHER -> {why}. Not recommended with these settings.")
    else:
        lines.append(f"  VERDICT: {m.upper()} -> {why}.")
        w = r['windows'][max(r['windows'], key=int)]; mine, other = (w['fixed_ratio'], w['fixed_margin']) if m == 'fixed ratio' else (w['fixed_margin'], w['fixed_ratio'])
        if mine['volume'] > other['volume']:
            lines.append(f"  (Disclosure: this mode traded more volume (${mine['volume']:,.0f} vs ${other['volume']:,.0f}), which helps the repo author's referral tier. The pick rests on the numbers above.)")
    lines.append(f"  Settings: fixed margin ${s['fixed_margin']['margin_per_entry']:,.0f} per entry (lead peaked at {s['fixed_margin']['max_concurrent_entries']} open entries)"
                 f" | fixed ratio copy amount ${s['fixed_ratio']['copy_amount']:,.0f} (ratio now {s['fixed_ratio']['ratio_now'] * 100:.2f}% of the lead)"
                 f" | caps ${s['caps']['per_symbol']:,.0f} per symbol, ${s['caps']['total']:,.0f} total | leverage {r['leverage']:.0f}x isolated, locked on: {', '.join(r['markets'])}")
    if r['too_small']: lines.append('  ** Your budget is too small for this lead: the margin per entry falls below the market minimum, so many copies would be skipped.')
    if r['too_deep']: lines.append(f"  ** Max drop deeper than your {r['max_dd'] * 100:.0f}% limit in: {', '.join(sorted(set(r['too_deep'])))}")
    return '\n'.join(lines)


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if '=' not in a]; kv = dict(a.split('=', 1) for a in sys.argv[1:] if '=' in a)
    if not args: sys.exit(__doc__)
    p = load_profile()
    if not p.get('balance'): sys.exit('Set your balance first: python3 scripts/profile.py set balance=<usd>')
    mk = markets(); (DATA / 'compare').mkdir(parents=True, exist_ok=True)
    print(f"Account ${F(p['balance']):,.0f}, up to {F(p['max_use']) * 100:.0f}% in use (${F(p['balance']) * F(p['max_use']):,.0f}), {F(p['leverage']):.0f}x, "
          f"fee {my_fee() * 100:.4f}%/side, excluded: {', '.join(p['excluded']) or 'none'}. Past results don't predict future ones.")
    for lead in args:
        r = analyse(lead, p, int(kv.get('days', 90)), mk)
        (DATA / 'compare' / f"{lead[:12]}.json").write_text(json.dumps(r, indent=1, default=str))
        print(report(r))
