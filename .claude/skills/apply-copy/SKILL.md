---
name: apply-copy
description: Set up a copy subscription the user has chosen: lock leverage and the profile's margin mode on the lead's markets, subscribe, set caps. Either via the API (after explicit yes for each change) or as manual steps in the Strike app. Use when the user says "set it up", "copy this trader", "subscribe".
---

# Set up a copy

Needs: a chosen lead and the settings from `compare.py` (`data/compare/<lead>.json`). If there's none, run
`check-trader` first. Never subscribe on a weekend just to "get in" quickly, or to win back a loss.

Ask with AskUserQuestion (header "How"): "How would you like to set this up?" Options:
"Through the API": I make each change, showing it first and asking before each one; "Myself in the app": you get
numbered steps with your exact numbers. No "(Recommended)" here: both are fine.

## A. Through the API (every step: dry run, explain, change menu from CLAUDE.md rule 4, then --yes)
Tell the user up front how many changes there are (e.g. 4) and number each menu "Change 1/4" and so on.
1. Lock leverage and margin mode (`margin_mode` in profile.json, isolated by default) on the lead's markets (from the compare output, minus excluded ones):
   `python3 scripts/strike_api.py set-margin-mode <isolated|cross> BTC-USD,ETH-USD,... [sub=<id>]` then
   `python3 scripts/strike_api.py set-leverage <lev> BTC-USD,ETH-USD,... [sub=<id>]`.
   Explain: a market with an open position keeps its old setting until it's flat.
2. Subscribe with the per-symbol cap and exclusions:
   - fixed margin: `python3 scripts/strike_api.py subscribe <lead> margin=<usd> cap=<usd> exclude=A-USD,B-USD [sub=<id>]`
   - fixed ratio: `python3 scripts/strike_api.py subscribe <lead> ratio=<usd> cap=<usd> exclude=... [sub=<id>]`
   New copies start with the lead's next trade: their positions already open aren't copied.
   The account must be funded first. A fixed-ratio copy amount is reserved from the available balance and can't exceed
   it (strike_api.py refuses before sending). If Strike still answers "invalid inputs", check the balance, the
   copy amount vs balance, and the lead's copier slots before retrying; don't retry blindly.
3. Set the total cap straight after with the new subscription id:
   `python3 scripts/strike_api.py caps <subscription_id> <per_symbol> <total> exclude=... [sub=<id>]`
   If the user skips a step, say what that means (e.g. skipping leverage: copies would use Strike's default leverage
   on those markets) and carry on; skipping the subscribe ends the setup.
4. Confirm with `python3 scripts/account.py` and read back mode, size, caps and exclusions.

## B. Manually in the app
Give numbered steps with their exact numbers:
1. Make sure the account (or sub-account) has the balance funded.
2. For each market the lead trades: open the market, set margin mode to the profile's mode (Isolated or Cross) and leverage to <lev>x before any
   position exists there.
3. Open the lead's page in Copy Trading -> Copy. Choose the mode, enter the margin per entry (fixed margin) or copy
   amount (fixed ratio).
4. Advanced settings: max margin per symbol <cap>, max total margin <total>, untick excluded markets.
5. Leave "copy existing positions" off. Confirm.
Then offer to verify it with `account.py`.

## After
Tell them what to expect (first copy lands on the lead's next order; check its size = margin x leverage) and
the review rhythm: `review` skill on their review day. End with the not-financial-advice line.
