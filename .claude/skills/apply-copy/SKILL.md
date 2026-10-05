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
2. Subscribe with both caps and the exclusions in one call:
   - fixed margin: `python3 scripts/strike_api.py subscribe <lead> margin=<usd> cap=<usd> tcap=<usd> exclude=A-USD,B-USD [sub=<id>]`
   - fixed ratio: `python3 scripts/strike_api.py subscribe <lead> ratio=<usd> cap=<usd> tcap=<usd> exclude=... [min_entry=<usd>] [mult=<1-10>] [sub=<id>]`
   New copies start with the lead's next trade: their positions already open aren't copied.
   The account must be funded first: Strike refuses a copy larger than the available balance, in both modes
   (strike_api.py checks before sending). If Strike still answers "invalid inputs", check the balance, the amount
   vs balance, and the lead's copier slots before retrying; don't retry blindly.
   Fixed ratio extras (only if the user wants them; explain first): **Minimum entry** raises any copied entry smaller
   than this trade value up to it (fewer copies skipped as too small, but small entries become bigger than the
   ratio says). **Ratio multiplier** (1-10x) multiplies every copied entry. Re-run the replay with
   `python3 scripts/replay.py <lead> fr=<amount> mult=<x> min_entry=<usd> cap=... tcap=...` to show the effect first.
   The per-symbol cap must be at least the margin per entry.
   If the user skips a step, say what that means (e.g. skipping leverage: copies would use Strike's default leverage
   on those markets, often 20x cross) and carry on; skipping the subscribe ends the setup.
3. Confirm with `python3 scripts/account.py` and read back mode, size, caps and exclusions.

## B. Manually in the app
Give numbered steps with their exact numbers, using the app's own labels (screenshots in docs/images/):
1. Fund the account (or sub-account) first: the copy button says "Insufficient available balance" until you do.
2. Open the trader's page (Copy Trading -> Explore, or the link `https://app.strikefinance.org/public-portfolio/<0x address>?platform=hyperliquid`)
   and press **Copy**.
3. Pick the tab: **Fixed Ratio** (enter the **Total allocation**) or **Fixed Margin** (enter the **Margin per copied entry**).
4. Open **Advanced settings**:
   - **Max total margin**: <total cap>. **Max margin per symbol**: <per-symbol cap>.
   - **Assets to copy**: untick each excluded market.
   - **Copy open positions on enter**: leave off.
   - Fixed ratio only: **Minimum entry** and **Ratio multiplier** only if agreed (see A.2); otherwise leave 0 and 1x.
   - **Margin mode & leverage** (the arrow on the right): one screen with every market. For each of the trader's
     markets set **Isolated** or **Cross** (the profile's margin mode) and the leverage, then press **Confirm** on that
     screen. Copies use these settings, not the trader's; Strike's defaults are often Cross 20x.
   - Press **Save** to return.
5. Press **Confirm Copy**.
Then offer to verify it with `account.py`.

## After
Tell them what to expect (first copy lands on the lead's next order; check its size = margin x leverage) and
the review rhythm: `review` skill on their review day. End with the not-financial-advice line.
