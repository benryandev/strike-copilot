---
name: review
description: Regular account review (weekly by default): subscriptions, P&L, leads' behaviour, drawdown vs limit, liquidations, leverage drift, plus checks for repo updates and Strike API spec changes. Use when the user says "review my account", "weekly check", "how am I doing", or on their review day.
---

# Review

1. `python3 scripts/update_check.py`:
   - Repo updates: summarise them in plain English (what changes for the user, not commit messages), then ask with
     the AskUserQuestion tool (header "Update", single choice) so it can't be skipped:
     "There are N updates to Strike Copilot. Install them before the review?" Options:
       - "Update now (Recommended)": installs the updates, then runs the review with them.
       - "Not this week": runs the review with the current version; I'll ask again next review.
       - "Show me more detail first": lists each update with one line on why it matters, then asks again.
     If an update fixes something that affects this user's account or a safety check, say so in the question.
     Only after "Update now" run `git pull -q` (no diffs or file listings: the user doesn't need them).
     Then re-read CLAUDE.md and the skills, since the rules may have changed, and mention any new setup question
     their profile hasn't answered yet.
   - Spec changes: mention them only if a change touches endpoints the scripts use (copy, account, positions,
     fills, portfolio, leverage, margin mode). Suggest checking GitHub for a repo update.
2. `python3 scripts/account.py` and explain the result in plain English: balance, drop from peak vs their
   limit, each subscription's P&L, what each lead did this week.
3. Act on "Needs attention" items calmly, one at a time:
   - Past drawdown limit: suggest pausing new copies (stop with "keep", or lower caps) and re-checking the lead
     with `check-trader`. Don't suggest adding funds or switching to win it back.
   - Lead idle: idle spells can be normal. Run `check-trader` on them; suggest a replacement only after about two
     idle weeks, or if they now fail checks.
   - Profit moving to markets Strike lacks: watch; two or three weeks running is a reason to stop.
   - Force-closed copies: explain what happened and check leverage/caps.
   - Leverage drift: offer the fix (dry run first).
4. If nothing needs attention, say so in one line. Don't invent tasks.
5. Remind them of the next review day (`review_day` in the profile). Suggest an extra review after: a lead's big
   loss, a liquidation, or any change they make.
