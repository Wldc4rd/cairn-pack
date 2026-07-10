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

## Continuity Protocol

Some travelers carry a gauge — a live reading of how much road is left before
the light fails. **This section binds ONLY the agents the city's `continuity`
config names with the gauge enabled.** If your turns carry no `context-gauge:`
line, you are not one of them: don't seed a notepad, don't self-hand-off, don't
adopt these thresholds. An agent with no gauge measuring for it has no business
managing its context by feel — that's cosplay, and it misleads the next
traveler. Everything below is for the gauged.

- **Boot from the stones, not from memory.** Starting a shift, read your
  **notepad** in full and your **latest terminated diary entry**, then open your
  first report with the 4-line boot echo — *in flight / settled (won't re-open) /
  waiting on / echo-token* — quoting the token from the notepad's first line. The
  token proves you read it; never guess it. Derive live state from the work
  tracker + notes, never from a remembered conversation.
- **The notepad is a snapshot, not a log.** Every line must be true *now*; order
  carries no time. Stamp each bullet `(M/D HHMMZ)` UTC at its start. It carries
  only what the tracker cannot: *settled* (with provenance — who ruled it, when,
  quoted, and its scope), gotchas, the *why*, the one-line *next*. Prune freely;
  git and the diary keep the history. Staleness is section-aware: in-flight/
  waiting lines go SUSPECT within the hour and need RE-ASSESS within a day;
  settled/gotcha lines age slower. Re-affirm by citing the check, not by
  refreshing a stamp.
- **The diary is the episodic WAL.** Append a line at each milestone, not only at
  death. At handoff, finish it (≤30 lines) and end with the terminator line
  exactly — no terminator, and your successor treats the entry as a crash
  artifact and trusts the tracker instead.
- **Milestones land in the tracker as you go.** The bead/issue store is the
  durable state; a fresh session rebuilds in-flight context from it — note at
  claim, decision, risk, and close.
- **Hand off on the gauge, not on nerves.** Below the normal threshold, deferring
  work "to save context" is prohibited — do it now or hand off now. In the
  handoff band, finish the current unit (or ~10 minutes, whichever is smaller),
  update the notepad, finish the diary, then `gc cairn continuity-handoff`. In
  the urgent band do that at once; past the ceiling, stop mid-unit — a stub diary
  with a terminator beats a truncated session. **Resetting is a shift change, not
  a failure.**

*Read the stones, add yours, hand off clean.*

## Hard rules

- **Never store credentials** — writes are secret-linted, but don't lean on
  the lint. Leave *where* a secret lives, never the secret.
- Memory is for durable knowledge, not scratch state: if it only matters this
  session, don't write it; if it's task tracking, it belongs in the work
  tracker, not the cairn.
- Keys are stable slugs: update the existing key rather than coining
  near-duplicates (`memory_search` first).

*Every traveler adds a stone.*
