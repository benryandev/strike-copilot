---
name: check-trader
description: Check one specific trader (Hyperliquid 0x address or Strike account id/profile) before copying. Use when the user names or pastes a trader, asks "is this trader any good", "should I copy X", or "compare modes for X".
---

# Check one trader

1. Get the lead's id: a Hyperliquid `0x...` address, or a Strike account id (uuid). If they paste a Strike profile
   link with an `addr1...` address, ask for the trader's page link from the copy-trading list instead, or look it up
   in `/v2/copy/discover?platform=strike` by `blockchain_address`.
2. `python3 scripts/lead.py <lead>`: explain each FAIL in one plain sentence (what it means for a copier, not jargon).
3. If it passes (or the user wants the numbers anyway): `python3 scripts/compare.py <lead>`, and present the results as in
   the `sweep` skill step 5.
4. If it fails, say so directly: "I wouldn't copy this trader with these settings, because ..." Don't soften a fail
   into a maybe. Offer a sweep for alternatives.
5. End with the not-financial-advice line.
