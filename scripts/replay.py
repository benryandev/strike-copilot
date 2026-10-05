"""Replay a lead as a Strike copier would have copied them, on a funded account, in either copy mode.

  python3 scripts/replay.py <lead> [days=90] [acct=<usd>] [fm=<margin per entry>] [fr=<copy amount>] [lev=10] [cap=<usd/symbol>] [tcap=<usd total>] [ex=PUMP-USD,...]

<lead> is a Hyperliquid 0x address or a Strike account id (uuid). Defaults come from profile.json.

How it works (each rule matches observed Strike behaviour):
- Only lead orders on markets Strike lists are copied, and only after the start.
- Closes take the same fraction off the copy as the lead took off their position (lead position is resynced to the real
  position before every order, so pre-start positions and missed fills don't distort it).
- Fixed ratio: copy size = lead order x (copy amount / lead equity at that moment), capped at 100%. Equity = Hyperliquid
  spot + perp account value (what Strike sizes from), or a Strike lead's account equity.
- Fixed margin: every opening or increasing lead order becomes one entry of `fm` margin at `lev`.
- An open is skipped when free margin is short, it is below the market's minimum order, a cap would be breached, or the market is excluded.
- Fees: your own taker fee per side (from your portfolio). Closes land 10 bp worse (copy-cost allowance).
- Open copies are marked at Hyperliquid 1h candle closes between orders, so drawdown includes open losses.
Not modelled: liquidations (isolated: one position's margin; cross: drawn from the shared balance), funding payments.
So the replay is the same for both margin modes; the difference is how a liquidation would play out.
"""
import sys, time, bisect, collections
from common import F, DAY, SLIP, get, hl, now_ms, day, markets, hl_map, load_profile, my_fee


def is_hl(lead): return lead.lower().startswith('0x')


# ---------- lead orders, keyed by Strike symbol ----------
def hl_fills(lead, t0, now, pages=50):
    fills, s = [], t0
    for _ in range(pages):  # userFillsByTime pages at 2,000
        r = hl({'type': 'userFillsByTime', 'user': lead, 'startTime': s, 'endTime': now, 'aggregateByTime': True}, list)
        fills += r
        if len(r) < 2000: break
        s = max(f['time'] for f in r) + 1; time.sleep(0.3)
    return sorted({(f['tid'], f['oid']): f for f in fills}.values(), key=lambda f: (f['time'], f['tid']))


def hl_to_orders(fills, mk):
    to_sym = hl_map(mk); orders = collections.OrderedDict()
    for f in fills:
        sym = to_sym.get(f['coin'])
        if not sym: continue
        o = orders.setdefault((sym, f['oid']), {'c': sym, 't': f['time'], 'q': 0.0, 'n': 0.0, 'sp': F(f['startPosition'])})
        o['q'] += F(f['sz']) * (1 if f['side'] == 'B' else -1); o['n'] += F(f['sz']) * F(f['px'])
    return sorted(orders.values(), key=lambda o: o['t'])


def hl_orders(lead, t0, now, mk):
    fills = hl_fills(lead, t0, now)
    return hl_to_orders(fills, mk), len(fills)


def strike_fills(acct, since=0, pages=200):
    fills, end = [], None
    for _ in range(pages):
        r = get(f'/v2/history/fill?account_id={acct}&limit=1000' + (f'&endTime={end}' if end else '')).get('fills', [])
        fills += r
        if len(r) < 1000 or min(f['timestamp'] for f in r) < since: break
        end = min(f['timestamp'] for f in r) - 1; time.sleep(0.2)
    return sorted({f['id']: f for f in fills}.values(), key=lambda f: (f['timestamp'], f['id']))


def strike_to_orders(fills, t0, now, mk):
    fills = [f for f in fills if not (f.get('client_order_id') or '').startswith('copy:')]  # judge the lead on their own trades
    pos = collections.defaultdict(float); orders = collections.OrderedDict()
    for f in fills:
        q = F(f['size']) * (1 if f['side'] == 'buy' else -1)
        if t0 <= f['timestamp'] <= now and f['symbol'] in mk:
            o = orders.setdefault((f['symbol'], f['order_id']), {'c': f['symbol'], 't': f['timestamp'], 'q': 0.0, 'n': 0.0, 'sp': pos[f['symbol']]})
            o['q'] += q; o['n'] += F(f['size']) * F(f['price'])
        pos[f['symbol']] += q
    return sorted(orders.values(), key=lambda o: o['t'])


def strike_orders(acct, t0, now, mk):
    fills = strike_fills(acct)
    return strike_to_orders(fills, t0, now, mk), len(fills)


def lead_orders(lead, t0, now, mk):
    return hl_orders(lead.lower(), t0, now, mk) if is_hl(lead) else strike_orders(lead, t0, now, mk)


def lead_equity(lead):
    """t -> lead equity (what fixed ratio divides by)."""
    if is_hl(lead):
        h = sorted((int(t), F(v)) for t, v in dict(hl({'type': 'portfolio', 'user': lead.lower()}, list))['allTime']['accountValueHistory'])
    else:
        h = [(int(r[0]), F(r[1])) for r in get(f'/v2/portfolio?account_id={lead}').get('history_perp_only', []) if F(r[1])]
    ht = [t for t, _ in h]
    return lambda t: h[max(bisect.bisect_right(ht, t) - 1, 0)][1] if h else 0.0


def marks(symbols, t0, now, mk, interval='1h'):
    out, unmarked = [], []
    for s in symbols:
        coin = mk[s]['hl']
        if not coin: unmarked.append(s); continue
        k = hl({'type': 'candleSnapshot', 'req': {'coin': coin, 'interval': interval, 'startTime': t0, 'endTime': now}}, list)
        out += [(x['T'], s, F(x['c'])) for x in k if x['T'] <= now]; time.sleep(0.2)
    return out, unmarked


class Lead:
    """Everything needed to replay one lead over one window; fetched once, replayed in as many modes as you like."""
    def __init__(self, lead, days, mk=None):
        self.lead, self.now = lead, now_ms(); self.t0 = self.now - int(days * DAY); self.mk = mk or markets()
        self.orders, self.nfills = lead_orders(lead, self.t0, self.now, self.mk)
        self.symbols = sorted({o['c'] for o in self.orders})
        self.marks, self.unmarked = marks(self.symbols, self.t0, self.now, self.mk)
        self.equity = lead_equity(lead)

    def fm(self, margin, lev):
        return lambda q, px, t: (1 if q > 0 else -1) * margin * lev / px

    def fr(self, amount):
        return lambda q, px, t: q * min(amount / self.equity(t), 1.0) if self.equity(t) > 0 else 0.0

    def run(self, start, size, lev=10, fee=None, cap=0, tcap=0, excluded=()):
        return replay(self.orders, self.marks, start, size, lev, my_fee() if fee is None else fee, cap, tcap, set(excluded), self.mk)


def replay(orders, mk_marks, start, size, lev, fee, cap, tcap, excluded, mk):
    lp = collections.defaultdict(float); cq = collections.defaultdict(float); ce = collections.defaultdict(float)
    last = {}; cash = start; pk = start; dd = 0.0; low = start; used_pk = 0.0; realised = fees = vol = 0.0; wiped = None
    n = collections.Counter(); sym_pk = collections.defaultdict(float); entries_pk = 0; lots = collections.Counter()
    ev = sorted([(o['t'], 1, o) for o in orders] + [(t, 0, (c, p)) for t, c, p in mk_marks], key=lambda e: (e[0], e[1]))
    eqf = lambda: cash + sum(cq[k] * (last[k] - ce[k]) for k in cq if cq[k])
    usedf = lambda: sum(abs(cq[k]) * last[k] / lev for k in cq if cq[k])

    def try_open(c, add, px):
        nonlocal cash, fees, vol
        if not add: return False
        m = lambda k: abs(cq[k]) * ce[k] / lev
        why = ('excluded' if c in excluded else 'tiny' if abs(add) * px < mk[c]['min_notional']
               else 'capped' if (cap and m(c) + abs(add) * px / lev > cap) or (tcap and sum(m(k) for k in cq if cq[k]) + abs(add) * px / lev > tcap)
               else 'no_margin' if eqf() - usedf() < abs(add) * px / lev else None)
        if why: n[why] += 1; return False
        ce[c] = (ce[c] * cq[c] + add * px) / (cq[c] + add) if cq[c] + add else px; cq[c] += add
        cash -= abs(add) * px * fee; fees += abs(add) * px * fee; vol += abs(add) * px; n['taken'] += 1; lots[c] += 1
        return True

    for t, kind, o in ev:
        if wiped: break
        if kind == 0:
            c, p = o
            if cq[c]: last[c] = p
        else:
            c, q = o['c'], o['q']
            if not q: continue
            px = o['n'] / abs(q); last[c] = px; lp[c] = o['sp']
            if lp[c] == 0 or lp[c] * q > 0:  # open or add
                try_open(c, size(q, px, t), px); lp[c] += q
            else:  # reduce / close / flip: close the same fraction of the copy
                frac = min(abs(q) / abs(lp[c]), 1.0)
                if cq[c]:
                    side = 1 if cq[c] > 0 else -1; cl = cq[c] * frac; xp = px * (1 - SLIP * side)
                    pnl = cl * (xp - ce[c]) - abs(cl) * xp * fee
                    cash += pnl; realised += pnl; fees += abs(cl) * xp * fee; vol += abs(cl) * xp; cq[c] -= cl
                rem = q + lp[c] if abs(q) > abs(lp[c]) else 0.0
                lp[c] = lp[c] + q if not rem else 0.0
                if abs(lp[c]) < 1e-12: lp[c] = 0.0; cq[c] = 0.0; lots[c] = 0
                if rem:
                    cq[c] = 0.0; lots[c] = 0
                    try_open(c, size(rem, px, t), px); lp[c] = rem
        if cq.get(c): sym_pk[c] = max(sym_pk[c], abs(cq[c]) * ce[c] / lev)
        entries_pk = max(entries_pk, sum(v for k, v in lots.items() if cq[k]))
        eq = eqf(); used_pk = max(used_pk, usedf()); pk = max(pk, eq); dd = min(dd, eq - pk); low = min(low, eq)
        if eq <= 0: wiped = day(t)
    eq = eqf()
    return dict(net=eq - start, realised=realised, open=eq - start - realised, dd=dd, dd_pct=dd / start if start else 0, low=low,
                peak_margin=used_pk, peak_entries=entries_pk, sym_pk=dict(sym_pk), fees=fees, volume=vol, wiped=wiped,
                taken=n['taken'], skipped=dict((k, v) for k, v in n.items() if k != 'taken'),
                open_now={k: round(cq[k] * last[k]) for k in cq if cq[k]})


def fmt(label, r, acct):
    sk = ', '.join(f'{v} {k}' for k, v in r['skipped'].items())
    return (f"{label}: net {r['net']:+,.0f} ({r['net'] / acct * 100:+.1f}%), lowest point ${r['low']:,.0f}, max drop {r['dd']:+,.0f} ({r['dd_pct'] * 100:.0f}%), "
            f"peak margin ${r['peak_margin']:,.0f}, fees ${r['fees']:,.0f} on ${r['volume']:,.0f} volume, {r['taken']} entries" + (f" (skipped: {sk})" if sk else '')
            + (f", WIPED OUT {r['wiped']}" if r['wiped'] else ''))


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__)
    p = load_profile(required=False); kv = dict(x.split('=', 1) for x in sys.argv[2:] if '=' in x)
    acct = F(kv.get('acct') or p['balance'] or 1000); lev = F(kv.get('lev') or p['leverage'] or 10)
    ex = [s for s in kv.get('ex', ','.join(p['excluded'])).split(',') if s]
    L = Lead(sys.argv[1], F(kv.get('days', 90)))
    print(f"lead {L.lead}: {len(L.orders)} orders on Strike markets in the last {kv.get('days', 90)} days ({', '.join(s[:-4] for s in L.symbols)}), "
          f"account ${acct:,.0f}, {lev:.0f}x, fee {my_fee() * 100:.4f}%/side" + (f"; marked at fills only: {L.unmarked}" if L.unmarked else ''))
    cap, tcap = F(kv.get('cap', 0)), F(kv.get('tcap', 0))
    if kv.get('fm'): print(fmt(f"fixed margin ${F(kv['fm']):,.0f}/entry", L.run(acct, L.fm(F(kv['fm']), lev), lev, cap=cap, tcap=tcap, excluded=ex), acct))
    if kv.get('fr'): print(fmt(f"fixed ratio ${F(kv['fr']):,.0f}", L.run(acct, L.fr(F(kv['fr'])), lev, cap=cap, tcap=tcap, excluded=ex), acct))
