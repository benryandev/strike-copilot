---
name: caps
description: Work out per-symbol and total cap settings for a lead or an existing subscription, compare a few options with replays, and optionally apply them. Use when the user asks about caps, "max margin per symbol", "max total margin", limiting exposure, or "what caps should I set".
---

# Caps

Strike's two caps (Advanced settings on a subscription): **max margin per symbol** (most margin the copy can put
in one market) and **max total margin** (most across all markets). Opens that would breach a cap are skipped;
closes always go through. Caps are checked on margin at entry.

1. Get the lead and mode/size (from an existing subscription via `account.py`, or the user's plan).
2. Baseline with no caps, then 3-4 options around it, e.g. for fixed margin $50/entry:
   `python3 scripts/replay.py <lead> days=90 fm=50` then `... fm=50 cap=150 tcap=500`, `cap=250 tcap=800`, ...
   (fixed ratio: `fr=<amount>` instead of `fm=`).
3. Show a small table: caps | net | lowest point | biggest drop | opens skipped by caps. Explain: caps that never bind
   change nothing in the past, so they're guards against the lead changing behaviour; caps that bind often cut
   both losses and winners. A useful starting point: per-symbol near the lead's own biggest single-market
   position in the replay, total around 1.5x the peak margin used, and never above balance x max_use.
4. Apply only via the change menu (CLAUDE.md rule 4): `python3 scripts/strike_api.py caps <subscription_id> <per_symbol> <total> [exclude=...] [sub=<id>]`
   (dry run first). Or give manual steps: Copy Trading -> your subscription -> Edit -> Advanced settings.
5. End with the not-financial-advice line.
