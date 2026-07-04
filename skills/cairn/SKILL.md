---
name: cairn
description: Use when storing or recalling durable agent memory through the memory_* MCP tools or `gc cairn` commands — writing down a hard-won fact, gotcha, or playbook so future sessions find it; leaving a handoff note for whoever resumes in-flight work; recalling or searching the brain hierarchy at session start or before re-deriving something; or deciding at which scope (agent, city, nation) a memory belongs.
---

# Cairn — stones on the road

Out in the wastes, travelers stack stones so the next one through doesn't lose
the road. Your memory is markdown notes in a nested `brain/` tree: your
**agent** cairn, your **city's** shared cairn, and the **nation** cairn the
whole fleet inherits. Every stone is timestamped, full-text searchable,
git-versioned, and human-editable — your operator can read and fix them in
Obsidian. Everything below is operator-neutral practice; your city's charter
may add stricter rules.

## The working loop

1. **Read the stones before you re-derive.** `memory_recall()` gives your
   ranked agent→city→nation digest (pinned first, then open handoffs, then
   newest). Your session-start head already carries the shared band; recall
   adds your personal scope.
2. **Search before you sink an evening.** `memory_search(query)` covers your
   chain; `scope: "city"` also spans sibling agents' cairns — a teammate may
   have paid for the fix already even if nobody promoted it.
3. **Add a stone when the road cost you something.** When you learn something
   a future session would otherwise rediscover the hard way — a root cause, a
   gotcha, a working recipe — `memory_remember(key, body)`. Same key + scope
   updates in place; write the fact, the why, and the pointer to deeper
   context.
4. **Leave a handoff when work is in flight.** `type: "handoff"` with the bead
   id (`bead: "ab-123"`) keeps your resume-state at the TOP of every recall
   until that bead closes — then it settles into the pile on its own. Refresh
   it at checkpoint moments; a stale handoff misleads the traveler after you.

## Choosing a scope

- **agent** (default): your own working knowledge — habits, calibration,
  agent-specific state.
- **city**: anything another agent in this settlement would benefit from.
  When in doubt between agent and city, choose city.
- **nation**: fleet-wide knowledge (cross-city gotchas, shared infra facts).
  Some cities route nation writes as proposals to the fleet steward — follow
  the refusal message if you get one.

Promotion is what puts knowledge into teammates' ambient recall; search makes
un-promoted knowledge findable. If you found a city-relevant fact buried in a
sibling's cairn, promote it (re-remember at city scope) so the next reader
doesn't need the search.

## Hard rules

- **Never store credentials** — writes are secret-linted, but don't lean on
  the lint. Leave *where* a secret lives, never the secret.
- Memory is for durable knowledge, not scratch state: if it only matters this
  session, don't write it; if it's task tracking, it belongs in the work
  tracker, not the cairn.
- Keys are stable slugs: update the existing key rather than coining
  near-duplicates (`memory_search` first).

*Every traveler adds a stone.*
