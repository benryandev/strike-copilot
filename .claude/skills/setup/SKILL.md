---
name: setup
description: First-time setup for Strike Copilot. Use when the user says "set me up", "get started", "first time", has no profile.json, or has no API key yet. Creates the API key safely, links the account, asks the profile questions, and offers the optional referral discount.
---

# Setup

Go one step at a time. Wait for the user after each step. Plain English.

## 1. Check the basics
- `python3 --version` (need 3.10+) and `python3 -c "import cryptography"`. If missing: `python3 -m pip install -r requirements.txt`.
  If pip is missing or fails, point them to the README's "What you need" section; don't improvise system installs.
- If `.secrets/strike_api.key` exists, skip to step 3.

## 2. Create the API key (on this computer)
1. Say: "I'll make a key on your computer. Only the public half goes to Strike; the private half stays in a hidden
   file here and is never shown, even to me."
2. Run `python3 scripts/strike_api.py keygen` and show only the PUBLIC key line.
3. Tell them: open app.strikefinance.org/api-keys while logged in to Strike (wallet or email), add a key, paste the public key,
   and pick an expiry. Mention: the key can trade and change settings but **cannot withdraw funds**.
4. Wait for "done". Then `python3 scripts/strike_api.py whoami`. If refused, check they pasted the public key and saved it.

Never ask for, display, or read the private key. If they paste one or a seed phrase, tell them to treat it as exposed.

## 3. Link the account
`python3 scripts/profile.py link`. If `whoami` shows sub-accounts, ask whether to copy from one. Recommend a
sub-account just for copying (keeps copies apart from anything they trade themselves). Sub-accounts are created in the app.

## 4. Referral discount (once, optional)
Skip this step if `profile.json` has `referral_asked` set. Otherwise run `python3 scripts/referral.py`.
- `has_referral_discount: true` -> say nothing about referrals. Move on.
- false -> ask exactly once, with the live number:
  "You aren't getting a referral fee discount at the moment. Would you like to use the BenRyan referral code for a
  {author_code_discount_pct}% discount on your trading fees? (BenRyan is the repo author's code; the author gets a share of the
  fees. It's optional and the tool works the same either way.)"
  - Yes: two ways, offer both:
    1. Open the `link` (https://app.strikefinance.org/trade/BTC?referralCode=BenRyan) while logged in to Strike and accept the code.
    2. Or go to the Referrals page (`manual_page`) and enter `BenRyan` (capital B and R) by hand.
    Then rerun `referral.py` to confirm the discount shows.
  - No: say "No problem" and never bring it up again.
  Either way, record it: `python3 scripts/profile.py set referral_asked=yes` (or `=no`).
This repo cannot set a code: Strike only allows it in the app.

## 5. Profile questions (multiple choice)
Ask with the AskUserQuestion tool so the user picks an answer instead of typing. "Other" is added automatically for
anything else. Put the suggested option first with "(Recommended)" in its label, and give every option a one-line
description of what it means for them. Two rounds:

**Round 1** (one AskUserQuestion call, four questions):
1. Balance, header "Balance": "How much will you fund the copy account with?" Options: $250, $500, $1,000, $5,000
   (no recommendation; "Other" covers any amount, e.g. $10,000). Description: sizes every suggestion. Four options is
   the tool's limit; the set matches real copiers (median active copier holds ~$435, 5 Oct 2026).
2. Leverage, header "Leverage": "What leverage should every market use?" Options: 10x (Recommended): each copy ties up
   a tenth of its size as margin; 5x: further from liquidation, needs twice the margin; 3x: very conservative;
   20x: liquidation comes much sooner.
3. Max in use, header "Max in use": "At most, how much of the balance should be in open copies at once?" Options:
   50% (Recommended): half stays free as a buffer; 25%: very cautious, fewer copies fit; 75%: more copies fit,
   less buffer. Becomes the total cap.
4. Dip limit, header "Max dip": "If the account dipped from its high, how big a dip could you sit through
   without wanting to stop?" Options: 20% (Recommended); 15%: only the calmest traders; 25%: a wider choice;
   30%: for bigger swings. Explain the limit only decides which traders get flagged.

**Round 2** (one call, four questions):
5. Excluded markets, header "Exclude", multiSelect: "Any markets you never want copied?" Options: None (Recommended
   if unsure); PUMP-USD: meme coin, very jumpy; NIGHT-USD: thin market, copies slip more; Stocks and commodities:
   every equity/metal/oil market (expand to the Strike symbols whose Hyperliquid twin starts with `xyz:`, from data/markets.json).
6. Copy mode, header "Mode": "Which copy mode do you prefer?" Options: Either (Recommended): I compare both for each
   trader and pick by profit per $ of drawdown; Fixed margin: same $ margin on every copied trade; Fixed ratio:
   copies scale with the trader's trade size relative to their account.
7. Margin mode, header "Margin": "How should copied trades share your money?" Options:
   Isolated (Recommended): each copied trade can only lose the margin put on it; a sharp move can liquidate one
   position even if the trader (often on cross) rides it out. Cross: copied trades share the whole balance, so
   single positions are liquidated less often, but one bad stretch can draw on the entire account, not just one
   trade's margin. Explain: Strike sets this per market, and it's locked once a position is open there.
8. Review day, header "Review day": "Which day each week should we review your account? I'll go over your copies,
   profit and loss, and how each trader is doing." Options: Monday (Recommended), Friday, Saturday, Sunday.

If the user asks what you recommend, give the recommended options and the reason in one line each. If they pick
"Other", accept any sensible value; ask again only if it can't be used (e.g. a share above 100%).
Save: `python3 scripts/profile.py set balance=... leverage=... max_use=... max_dd=... excluded=... mode_pref=... margin_mode=... review_day=... [sub_account_id=...]`
(max_use and max_dd as shares, e.g. 0.5 and 0.2; mode_pref = either | fixed_margin | fixed_ratio).
Then `python3 scripts/profile.py show` and show the saved settings as a short table, plus their fee.

## 6. Next
Offer: "Want me to run a full sweep for traders that fit your account? It takes 20-30 minutes." -> `sweep` skill.
