"""Full sweep of copyable leads: Hyperliquid wallets Strike can copy, plus Strike-native traders. ~20-30 minutes.

  python3 scripts/sweep.py            Strike's scored Hyperliquid set (~5K wallets) + Strike-native traders
  python3 scripts/sweep.py wide       the whole Hyperliquid leaderboard (~45K wallets) instead: slower, finds more
  python3 scripts/sweep.py top        print the shortlist from the latest sweep without re-running

Results: data/sweep/<date>/shortlist.json (resumable: re-running the same day picks up where it stopped).

Funnel (each step is a lesson from copying for real, not a guess):
 1. Strike's numbers pre-filter only (profitable, recent, not a market maker, account $30K-$5M for Hyperliquid).
    Strike's copy score, ROI and "copyable share" are never trusted on their own: they include spot, and "copyable" counts listing, not liquidity.
 2. Hyperliquid account history: perp trading must be where the profit comes from (>=70% this month, >=50% all time),
    account at least 180 days old, perp account >= $20K, never wiped out (fell below 5% of a $10K+ peak).
 3. Fills, last 90 days: >=70% of realised profit on markets Strike lists, >=20 round trips, lead edge >= 20 bp per $
    (a copy round trip costs ~15 bp in fees and price gap), and a fixed-margin copy (with your fee) positive over both
    the last 90 and last 30 days.
 4. Fits your budget: margin per entry = (balance x max_use) / the most entries the lead held at once, at least $5.
Ranked by return per $ of drawdown in the 90-day copy simulation. Then run compare.py on the top few.
"""
import sys, os, json, time, datetime, collections
from common import F, DAY, DATA, get, hl, now_ms, markets, hl_map, load_profile, my_fee, SLIP
from replay import hl_to_orders, hl_fills, strike_fills, strike_to_orders

NOW = now_ms(); T90 = NOW - 90 * DAY; T30 = NOW - 30 * DAY
WIDE = 'wide' in sys.argv[1:]
OUT = DATA / 'sweep' / (datetime.date.today().isoformat() + ('-wide' if WIDE else ''))
ENTRY = 2000  # notional per simulated entry ($200 margin at 10x); only the shape matters, compare.py sizes to you


def P(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True)
    with open(OUT / 'progress.log', 'a') as f: f.write(s + '\n')


def cached(name, fn):
    fp = OUT / name
    if fp.exists(): return json.loads(fp.read_text())
    v = fn(); fp.write_text(json.dumps(v)); return v


def g(d, *ks, default=0.0):
    for k in ks:
        if not isinstance(d, dict) or d.get(k) is None: return default
        d = d[k]
    try: return F(d)
    except (TypeError, ValueError): return default


# ---------- quick copy simulation on lead orders (fixed margin, realised only; compare.py does the full replay) ----------
def quick(orders, start, fee):
    mine = collections.defaultdict(lambda: [0.0, 0.0]); n = collections.Counter(); trip = {}
    real = cum = pk = mdd = 0.0; maxn = 0; trips = []
    for o in orders:
        if o['t'] < start or not o['q']: continue
        c, q, sp = o['c'], o['q'], o['sp']; px = o['n'] / abs(q)
        if sp == 0 or sp * q > 0:  # open or add: one more entry
            if sp == 0: trip[c] = 0.0
            if c in trip:
                mine[c][0] += (1 if q > 0 else -1) * ENTRY / px; mine[c][1] += ENTRY; n[c] += 1; maxn = max(maxn, sum(n.values()))
        else:  # reduce, close or flip: close the same fraction of the copy
            frac = min(abs(q) / abs(sp), 1.0); mq = mine[c][0] * frac; cost = mine[c][1] * frac; side = 1 if sp > 0 else -1
            if mq:
                pnl = mq * px * (1 - SLIP * side) - side * cost - (abs(mq) * px + cost) * fee
                real += pnl; cum += pnl; pk = max(pk, cum); mdd = min(mdd, cum - pk); trip[c] = trip.get(c, 0.0) + pnl
            mine[c][0] -= mq; mine[c][1] -= cost
            if abs(q) >= abs(sp):  # flat (or flipped)
                if c in trip: trips.append(trip.pop(c))
                mine[c] = [0.0, 0.0]; n[c] = 0
                if abs(q) > abs(sp):
                    rem = q + sp; trip[c] = 0.0; mine[c] = [(1 if rem > 0 else -1) * ENTRY / px, ENTRY]; n[c] = 1
    return dict(net=real, mdd=mdd, entries=maxn, trips=len(trips), win=sum(t > 0 for t in trips) / len(trips) if trips else 0)


def lead_edge(orders, start):
    """Lead's own realised P&L per $ opened on Strike markets, in bp. Positions opened before the window are ignored (entry price unknown)."""
    cost = {}; pnl = opened = 0.0
    for o in orders:
        if o['t'] < start or not o['q']: continue
        c, q, sp = o['c'], o['q'], o['sp']; px = o['n'] / abs(q)
        if sp == 0 or sp * q > 0:
            if sp == 0: cost[c] = px
            elif c in cost: cost[c] = (cost[c] * abs(sp) + px * abs(q)) / (abs(sp) + abs(q))
            if c in cost: opened += abs(q) * px
        else:
            if c in cost: pnl += min(abs(q), abs(sp)) * (px - cost[c]) * (1 if sp > 0 else -1)
            if abs(q) > abs(sp): cost[c] = px; opened += abs(q + sp) * px
            elif abs(q) == abs(sp): cost.pop(c, None)
    return pnl / opened * 1e4 if opened else 0.0


def wipes(hist):
    peak = 0; w = 0; below = False
    for _, v in hist:
        v = F(v)
        if v > peak: peak = v; below = False
        if peak >= 10000 and v < 0.05 * peak and not below: w += 1; below = True
    return w


# ---------- Hyperliquid leads ----------
def hl_universe():
    pre = []
    if WIDE:
        lb = cached('hl_leaderboard.json', lambda: get('https://stats-data.hyperliquid.xyz/Mainnet/leaderboard').get('leaderboardRows', []))
        P('universe: Hyperliquid leaderboard', len(lb))
        for row in lb:
            wp = {k: v for k, v in row['windowPerformances']}; eq = F(row['accountValue'])
            if 30000 <= eq <= 5e6 and F(wp['month']['pnl']) > 0 and F(wp['allTime']['pnl']) > 0 and F(wp['week']['vlm']) > 0:
                pre.append(dict(addr=row['ethAddress'].lower(), eq=eq, pnl30=F(wp['month']['pnl'])))
    else:
        def pull():
            items, cur = {}, None
            for page in range(60):
                d = get('/v2/copy/discover?top=10000&rank_by=pnl_30d&sort=copy_score&order=desc&pnl_window=30d&limit=200' + (f'&cursor={cur}' if cur else ''))
                for it in d.get('items', []): items[it['address']] = it
                cur = d.get('next_cursor')
                if not cur or not d.get('items'): break
                time.sleep(0.3)
            return items
        items = cached('hl_discover.json', pull); P('universe: Strike discover (Hyperliquid)', len(items))
        for a, it in items.items():
            bd = it.get('copy_score_breakdown') or {}
            dd30 = g(it, 'metrics', 'max_drawdown', '30d', default=None); dd30 = g(bd, 'drawdown_30d', 'raw', default=1) if dd30 is None else dd30
            hrs = (NOW - (it.get('last_traded_at_ms') or 0)) / 3.6e6
            if (g(it, 'roi', '30d') > 0 and g(it, 'pnl', 'all') > 0 and dd30 <= 0.25 and 30000 <= g(it, 'account_value') <= 5e6 and hrs < 96
                    and it.get('style') not in ('market_maker', 'inactive', 'unknown')):
                pre.append(dict(addr=a.lower(), eq=g(it, 'account_value'), pnl30=g(it, 'pnl', '30d')))
    P('step 1 survivors', len(pre)); return pre


def hl_stage(pre, fee, mk):
    sa = OUT / 'hl_stageA.json'; A = json.loads(sa.read_text()) if sa.exists() else {}
    for i, r in enumerate(pre):
        if r['addr'] in A: continue
        try:
            pf = dict(hl({'type': 'portfolio', 'user': r['addr']}, list)); st = hl({'type': 'clearinghouseState', 'user': r['addr']}, dict)
        except RuntimeError: continue
        d = lambda k: (lambda p: p[-1] - p[0] if p else 0)([F(x[1]) for x in pf[k]['pnlHistory']])
        mt, mp, at, ap = d('month'), d('perpMonth'), d('allTime'), d('perpAllTime'); h = pf['allTime']['accountValueHistory']
        A[r['addr']] = dict(r, perpM=mp / mt if mt else 0, perpA=ap / at if at else 0, perpMonthPnl=mp, perpAcct=F(st['marginSummary']['accountValue']),
                            ageDays=(NOW - (h[0][0] if h else NOW)) / DAY, wipes=wipes(h))
        if i % 25 == 0: P(f'step 2: {i}/{len(pre)}'); sa.write_text(json.dumps(A))
        time.sleep(0.25)
    sa.write_text(json.dumps(A))
    surv = [r for r in A.values() if r['perpM'] >= 0.7 and r['perpA'] >= 0.5 and r['perpAcct'] >= 20000 and r['ageDays'] >= 180
            and r['perpMonthPnl'] > 0 and r['wipes'] == 0]
    P('step 2 survivors (Hyperliquid)', len(surv))
    out = []; to_sym = hl_map(mk)
    for i, r in enumerate(surv):
        fp = OUT / 'fills' / f"{r['addr'][:12]}.json"
        if fp.exists(): fills = json.loads(fp.read_text())
        else:
            try: fills = hl_fills(r['addr'], T90, NOW, pages=8)
            except RuntimeError: P(f"  {r['addr'][:10]} fills failed (rate limit), skipped"); continue
            fp.write_text(json.dumps(fills)); time.sleep(0.3)
        tot = sum(F(x['closedPnl']) for x in fills if not x['coin'].startswith('@'))
        lst = sum(F(x['closedPnl']) for x in fills if x['coin'] in to_sym)
        out.append(dict(r, platform='hyperliquid', listed_share=lst / tot if tot > 0 else 0, **metrics(hl_to_orders(fills, mk), fee)))
        if i % 25 == 0: P(f'step 3: {i}/{len(surv)}')
    return out


# ---------- Strike-native leads ----------
def strike_stage(fee, mk):
    def pull():
        items, cur = {}, None
        for _ in range(60):
            d = get('/v2/copy/discover?platform=strike&limit=200&sort=copy_score&order=desc' + (f'&cursor={cur}' if cur else ''))
            for it in d.get('items', []): items[it['address']] = it
            cur = d.get('next_cursor')
            if not cur or not d.get('items'): break
            time.sleep(0.2)
        return items
    items = cached('strike_discover.json', pull); P('universe: Strike-native traders', len(items))
    pre = [it for it in items.values() if g(it, 'pnl', '30d') > 0 and g(it, 'pnl', 'all') > 0 and g(it, 'account_value') >= 1000
           and (NOW - (it.get('last_traded_at_ms') or 0)) < 96 * 3.6e6]
    P('step 1 survivors (Strike)', len(pre)); out = []
    for i, it in enumerate(pre):
        a = it['address']; fp = OUT / 'fills' / f"s_{a[:12]}.json"
        if fp.exists(): fills = json.loads(fp.read_text())
        else: fills = strike_fills(a, since=T90, pages=20); fp.write_text(json.dumps(fills))
        pf = get(f'/v2/portfolio?account_id={a}'); h = [(r[0], r[1]) for r in pf.get('history_perp_only', []) if F(r[1])]
        r = dict(addr=a, platform='strike', nickname=it.get('nickname'), eq=g(it, 'account_value'), pnl30=g(it, 'pnl', '30d'),
                 ageDays=(NOW - h[0][0]) / DAY if h else 0, wipes=wipes(h), listed_share=1.0)
        own = [f for f in fills if not (f.get('client_order_id') or '').startswith('copy:')]
        out.append(dict(r, copy_share=1 - len(own) / len(fills) if fills else 0, **metrics(strike_to_orders(fills, T90, NOW, mk), fee)))
        if i % 25 == 0: P(f'Strike step 3: {i}/{len(pre)}')
        time.sleep(0.2)
    return out


def metrics(orders, fee):
    s90, s30 = quick(orders, T90, fee), quick(orders, T30, fee)
    return dict(orders=len(orders), opd=len(orders) / 90, markets=sorted({o['c'] for o in orders}), edge_bp=lead_edge(orders, T90),
                sim90=s90, sim30=s30)


def shortlist(rows, p):
    budget = F(p['balance'] or 1000) * F(p['max_use'])
    ok = []
    for r in rows:
        why = []
        if r['listed_share'] < 0.7: why.append('profit mostly on markets Strike lacks')
        if r['wipes']: why.append('wiped out before')
        if r['platform'] == 'strike' and r['ageDays'] < 14: why.append('too new')
        if r['sim90']['trips'] < 20: why.append('under 20 round trips')
        if r['edge_bp'] < 20: why.append('edge too thin to survive copy costs')
        if r['sim90']['net'] <= 0 or r['sim30']['net'] <= 0: why.append('copy loses money in a window')
        r['margin_fit'] = budget / max(r['sim90']['entries'], 1)
        if r['margin_fit'] < 5: why.append('stacks too many entries for your budget')
        r['score'] = r['sim90']['net'] / max(-r['sim90']['mdd'], ENTRY * 0.05)
        r['rejected'] = why
        if not why: ok.append(r)
    return sorted(ok, key=lambda r: -r['score'])


def show(top, n=15):
    print(f"\n{'#':>2} {'lead':44} {'where':11} {'net/DD':>6} {'edge bp':>7} {'orders/d':>8} {'entries':>7} {'$/entry':>7}  markets")
    for i, r in enumerate(top[:n], 1):
        print(f"{i:>2} {r['addr']:44} {r['platform']:11} {r['score']:>6.1f} {r['edge_bp']:>7.0f} {r['opd']:>8.1f} {r['sim90']['entries']:>7} {r['margin_fit']:>7.0f}  "
              + ', '.join(m[:-4] for m in r['markets'][:8]))
    print('\nNext: python3 scripts/compare.py <lead> for the top few (full replay sized to your profile).')


if __name__ == '__main__':
    p = load_profile()
    if 'top' in sys.argv[1:]:
        runs = sorted((DATA / 'sweep').glob('*/shortlist.json'))
        if not runs: sys.exit('No sweep yet. Run: python3 scripts/sweep.py')
        print(f'Sweep of {runs[-1].parent.name}'); show(shortlist(json.loads(runs[-1].with_name('all.json').read_text()), p)); sys.exit()
    (OUT / 'fills').mkdir(parents=True, exist_ok=True)
    fee = my_fee(); mk = markets(refresh=True)
    P(f'sweep {OUT.name}, fee {fee * 100:.4f}%/side, {len(mk)} Strike markets')
    rows = hl_stage(hl_universe(), fee, mk) + strike_stage(fee, mk)
    (OUT / 'all.json').write_text(json.dumps(rows))
    top = shortlist(rows, p); (OUT / 'shortlist.json').write_text(json.dumps(top, indent=1))
    P(f'DONE: {len(rows)} leads checked in full, {len(top)} pass every check')
    show(top)
