---
name: sweep
description: Full sweep of Hyperliquid and Strike traders to find leads that fit the user's account, then compare fixed margin vs fixed ratio on the best few. Use when the user asks who to copy, wants recommendations, "find me traders", or "run a sweep".
---

# Sweep

Needs a profile (`profile.json` with a balance). If missing, run the `setup` skill first.

1. Tell the user: "This checks a few thousand traders against the lessons from copying for real. It takes 20-30
   minutes; I'll come back with the shortlist."
2. Run in the background: `python3 scripts/sweep.py` (add `wide` only if they ask for the widest search, ~1 hour).
   Same-day reruns resume from the cache in `data/sweep/<date>/`.
3. When done, read the shortlist it prints (or `python3 scripts/sweep.py top`). If nothing passes, say so plainly:
   no lead is better than a bad lead. Offer to re-run next week, and don't loosen the checks to produce a name.
4. Run `python3 scripts/compare.py <lead1> <lead2> <lead3>` on the top 3 (one call; it takes a few minutes).
5. Present 2-3 options. For each, in plain English:
   - who they are (Hyperliquid wallet or Strike trader, markets they trade, orders per day, how long they hold)
   - the 90-day and 30-day results for both modes: net profit after fees, lowest point, biggest drop
   - the verdict and the reason in plain words (CLAUDE.md rule 7: profit per $ of drawdown, smaller drawdown on ties),
     plus the volume disclosure line whenever compare.py prints it
   - the catch: every lead has one (losing months, concentration in one market, few copier slots, idle spells)
   - settings: mode, margin per entry or copy amount, max concurrent entries, caps, exclusions, leverage to lock
   - copier slots used out of 40
6. End with the not-financial-advice line, then offer the `apply-copy` skill.
