"""Read-only referral check: is your account getting a referral fee discount, and what the repo author's code would give you.

  python3 scripts/referral.py            uses the account id in profile.json
  python3 scripts/referral.py <account_id>

This never changes anything. Strike only lets you link a code yourself, in the app while logged in
(the API key can't do it). If you already have a code, this tool leaves it alone.
"""
import sys, json
from common import get, load_profile

CODE = 'BenRyan'  # the repo author's code (case-sensitive). Using it is optional; the author earns a share of the fees it discounts.
LINK = f'https://app.strikefinance.org/trade/BTC?referralCode={CODE}'  # opening this while logged in offers the code
MANUAL = 'https://app.strikefinance.org/referrals'  # or type the code in on the Referrals page


def main(a):
    acct = a[0] if a else load_profile(required=False).get('account_id')
    if not acct: sys.exit('No account id yet: run python3 scripts/profile.py link first.')
    pf = get(f'/v2/portfolio?account_id={acct}')
    if 'fee_discount_rate' not in pf: sys.exit(f'Could not read the portfolio for {acct}: {pf}')
    have = float(pf.get('fee_discount_rate') or 0)
    ref = get(f'/v2/referral?code={CODE}')
    offer = float(ref.get('referee_discount_rate') or 0) * 100
    print(json.dumps({'has_referral_discount': have > 0, 'current_discount_pct': have,
                      'author_code': CODE, 'author_code_discount_pct': offer, 'author_code_tier': ref.get('tier_name'),
                      'link': LINK, 'manual_page': MANUAL}, indent=1))


if __name__ == '__main__':
    main(sys.argv[1:])
