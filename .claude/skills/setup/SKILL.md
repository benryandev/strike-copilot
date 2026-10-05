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
3. Tell them: open app.strikefinance.org/api-keys with their wallet connected, add a key, paste the public key,
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
  - Yes: give them the `link`, and say: open it with your wallet connected and accept the code (or enter `BenRyan`,
    capital B and R, in the Referrals page). Then rerun `referral.py` to confirm the discount shows.
  - No: say "No problem" and never bring it up again.
  Either way, record it: `python3 scripts/profile.py set referral_asked=yes` (or `=no`).
This repo cannot set a code: Strike only allows it in the app.

## 5. Profile questions (ask one or two at a time, explain why each matters)
1. **Balance**: "How much will you fund the copy account with (USD)?" -> sizes every recommendation.
2. **Leverage**: "What leverage do you want on every market? 10x is a common middle ground: each copy's margin is a
   tenth of its size." Explain higher = closer liquidations. Default 10.
3. **Max in use**: "At most, how much of the balance should be in open copies at once? Half is a sensible start."
   -> becomes the total cap. Default 0.5.
4. **Drawdown limit**: "How big a drop from the account's peak could you sit through without stopping? e.g. 20%."
   -> leads that went deeper in replays get flagged. Default 0.25.
5. **Excluded markets**: "Any markets you never want copied?" (e.g. PUMP-USD; thin or meme markets). Default none.
6. **Mode preference**: fixed margin, fixed ratio, or no preference ("either"). Explain in one line each:
   fixed margin = same $ margin on every copied entry; fixed ratio = copies scale with the lead's size relative to their account.
7. **Review day**: which day each week to review. Default Monday.
Save: `python3 scripts/profile.py set balance=... leverage=... max_use=... max_dd=... excluded=... mode_pref=... review_day=... [sub_account_id=...]`
Then `python3 scripts/profile.py show` (also prints their fee).

## 6. Next
Offer: "Want me to run a full sweep for traders that fit your account? It takes 20-30 minutes." -> `sweep` skill.
