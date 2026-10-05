"""Your copy-trading profile (profile.json, stays on this computer). Claude fills it from the setup questions.

  python3 scripts/profile.py show
  python3 scripts/profile.py link                       read your account id from the API key (run after the key is added on Strike)
  python3 scripts/profile.py set balance=1000 leverage=10 max_use=0.5 max_dd=0.25 excluded=PUMP-USD,NIGHT-USD sub_account_id=<id>

Fields:
  balance      USD you will fund the copy account with
  leverage     leverage to lock on every market before copying (10 = each copy's margin is 1/10 of its size)
  max_use      share of the balance allowed in open copies at once (0.5 = half); sets the total cap
  max_dd       the drop from peak you could sit through without stopping (0.25 = 25%); leads that went deeper in replays are flagged
  excluded     markets you never want copied, e.g. PUMP-USD
  sub_account_id  copy from this sub-account instead of the main account (recommended: keeps copying separate)
  mode_pref    fixed_margin | fixed_ratio | either
  margin_mode  isolated (each copy risks only its own margin) | cross (copies share the balance)
  review_day   day of the week for your regular review
  referral_asked  yes/no once the optional referral question has been asked (it is never asked again)
"""
import sys, json
from common import load_profile, save_profile, fee_rate

NUM = {'balance', 'leverage', 'max_use', 'max_dd'}


def main(a):
    if not a or a[0] == 'show':
        p = load_profile(required=False); print(json.dumps(p, indent=1))
        if p.get('account_id'):
            f, parts = fee_rate(p['account_id'])
            print(f"\nYour fee per side: {f * 100:.4f}% (tier {parts['tier']} base {parts['base'] * 100:.3f}%, "
                  f"staking discount {parts['staking'] * 100:.0f}%, referral discount {parts['referral'] * 100:.0f}%)")
        return
    p = load_profile(required=False)
    if a[0] == 'link':
        from strike_api import request
        r = request('GET', '/v2/account')
        if not isinstance(r, dict) or 'account_id' not in r: sys.exit(f'The key was refused: {r}')
        p['account_id'] = r['account_id']; save_profile(p); print('linked account', r['account_id']); return
    if a[0] != 'set': sys.exit(__doc__)
    for kv in a[1:]:
        k, v = kv.split('=', 1)
        if k not in p: sys.exit(f'unknown field {k}\n{__doc__}')
        if k in NUM: v = float(v)
        elif k == 'excluded': v = [s if s.endswith('-USD') else s + '-USD' for s in v.upper().split(',') if s]
        elif v in ('', 'none', 'None'): v = None
        p[k] = v
    if p['max_use'] and not 0 < p['max_use'] <= 1: sys.exit('max_use is a share between 0 and 1, e.g. 0.5 for half')
    if p['margin_mode'] not in ('isolated', 'cross'): sys.exit('margin_mode is isolated or cross')
    if p['max_dd'] and not 0 < p['max_dd'] <= 1: sys.exit('max_dd is a share between 0 and 1, e.g. 0.25 for 25%')
    save_profile(p); print(json.dumps(p, indent=1))


if __name__ == '__main__':
    main(sys.argv[1:])
