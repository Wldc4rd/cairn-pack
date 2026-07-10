# Cairn — vault-backed agent memory for Gas City

```
                      .
                     /=\
                    /===\
                   /=====\        C A I R N
                  /=======\
                 .---------.      every traveler adds a stone
              __/  o   O  o \__
           __/  O   ._____.  O \__
          /   o   _/       \_  o  \
         '------'             '-----'
```

*Out in the wastes, wanderers stack stones to mark the road — for the learned
and the lost. A memory is a **stone on the cairn**: dated, findable, standing
after the traveler moves on. Each scoped brain is its own cairn on the road;
a fresh session reads the stones of those who walked before it.*

Flat key/value agent memories fail at scale: no timestamps, no ranking, and a
session-start dump a host truncates silently — dropping exactly the newest
hand-off. Cairn replaces that with **one markdown note per memory** in a nested
brain hierarchy, and a **token-budgeted, ranked recall digest** that cannot
truncate away what matters. Any folder of markdown is an Obsidian vault, so
every stone is timestamped, searchable, versioned, and human-editable for free.

```
Gas Cities/                          # the tree (write_root points at its base)
  brain/                             # NATION — the cairn every city inherits
  <city>/
    brain/                           # CITY — this settlement's shared cairn
    agents/<agent>/brain/            # AGENT — one traveler's own stones
```

- **Recall is a scope chain**: agent → city → nation, most-specific-first with
  key-dedup (an agent-scope stone shadows a same-key broader one). Ranked
  **pinned → open handoffs → newest**, budget-bounded, with an explicit
  "N more via search" tail.
- **Handoffs are first-class**: `type: handoff` + a bead id keeps a stone at
  the top of every recall until the bead closes — then it settles into the
  pile on its own.
- **Humans co-own the cairn**: open the tree in Obsidian; edit or fix any
  note — the edit *is* the memory.

## What's new in v0.2.0 — the continuity layer

Cairn's mission was always agent continuity; v0.2.0 adds the layer the memory
hierarchy was missing — the **working-memory / boot layer**. The disease it
cures, observed in the field: after a session reset, fresh agents boot lost and
re-litigate settled decisions; mid-session, agents that cannot see their own
context develop *context anxiety* — sometimes at barely half-full — go
lethargic, promise to "finish in a fresh session," and never actually reset.
Those are one loop, and it breaks in two places: **deterministic boot** (a
notepad read by path, never by ranking) and an **externalized reset decision**
(a measured gauge + hard thresholds instead of nerves).

New in this release, all **default-OFF**:

- **notepad** — per-agent working-memory head: snapshot semantics, per-line UTC
  stamps with section-aware TTLs, a rotating **echo-token** that makes the boot
  echo falsifiable (an echo without the token is a confabulated boot).
- **diary** — episodic WAL: milestone-appended entries, terminator-marked (no
  terminator ⇒ successors treat it as a crash artifact and trust the tracker).
- **context gauge** — the agent's *real* context %, teed into each turn, with a
  dynamic per-session window (no hand-set denominators) and honest labels when
  it must fall back (`(mapped)`, `(inferred)`, `(assumed)`).
- **commands** — `continuity-init` / `continuity-status` / `continuity-handoff`.
- 20 new hermetic tests; the 36 existing memory tests unbroken.

The design was adversarially red-teamed twice and exercised end-to-end before
packaging — including a live mid-task handoff fired at the urgent threshold
during the very build that produced this release.

## Quick start

```sh
gc import add /path/to/cairn                 # registers MCP, commands, skill, order
gc cairn init --write-root "/path/to/vault/Gas Cities" --mode vault
gc cairn enable --reviewed-by <your-mayor>   # default-OFF until reviewed
gc cairn remember my-first-key "the fact worth keeping" --scope city
gc cairn recall
```

Agents get `memory_remember / memory_recall / memory_search / memory_forget`
MCP tools at their next session (re)spawn; every tool has a CLI twin so
non-MCP contexts (orders, hooks, humans) are never locked out.

## Session-start digest (the PRIME.md region)

The shipped order `cairn-recall` (5m cooldown) rewrites **only** a
marker-fenced region of `.beads/PRIME.md` with the shared city+nation band:

```
<!-- brain-recall:begin -->
…generated digest…
<!-- brain-recall:end -->
```

Three deliberate gates, all default-off: the pack enable marker
(`.gc/cairn.enabled`), the recall arm marker (`.gc/cairn-recall.enabled`), and
the markers themselves — the command refuses to touch a PRIME.md that has no
region. Adding the markers is your explicit cutover act. The digest's first
line tells fresh sessions to run `gc cairn recall` for their personalized
agent-scope chain.

## Continuity (context gauge + handoff protocol)

An optional layer for agents that burn long sessions: a **context-usage gauge**
that tees the agent's *real* context % into each turn (so it acts on data, not
transcript "feel"), plus a disciplined **notepad + diary + handoff** protocol so
a fresh session recovers state from a durable WAL instead of memory. **Default-
OFF and triple-gated** — the pack enable marker, `continuity.enabled`, and the
session's agent being listed in `continuity.enabled_agents`. An unlisted agent
gets no gauge and is explicitly exempt from the protocol (anti-cosplay).

```sh
# 1) configure: add a "continuity" block to .gc/services/cairn/config.json
#    (see examples/config.example.json): enabled + enabled_agents + thresholds.
# 2) seed each in-scope agent's notepad + diary (idempotent):
gc cairn continuity-init
# 3) inspect health any time (notepad age, TTL tiers, diary, gauge heartbeat):
gc cairn continuity-status
# 4) the disciplined handoff (verify diary -> rotate echo-token -> pointer body):
gc cairn continuity-handoff --bead <id>
```

The gauge needs the true context-window size, which Claude Code hands **only** to
the statusline — so the two host-event pieces install **out-of-pack**, in the
operator's `~/.claude/settings.json` (no pack ships host hooks today):

```json
{
  "statusLine": { "type": "command", "command": "python3 /path/to/cairn/scripts/statusline-tee.py" },
  "hooks": { "UserPromptSubmit": [ { "hooks": [ { "type": "command",
    "command": "python3 /path/to/cairn/scripts/context-gauge.py" } ] } ] }
}
```

`statusline-tee.py` caches the window and delegates rendering to your existing
statusline unchanged; `context-gauge.py` reads that cache and emits the gauge
line. Both fail-silent — unwired or disabled, nothing changes. Thresholds, TTLs,
and the model→window map all live in the `continuity` config block.

### Deploying v0.2.0 to a city (mayor's checklist)

> **⚠️ Clone-dir gotcha (first fleet deploy found it):** the pack directory
> **must be named `cairn`** (it must match `pack.toml`'s `name`). GitHub's
> default clone dir is `cairn-pack`, which registers the import as `cairn-pack`
> and makes `gc cairn` an unknown command. Clone with an explicit target:
>
> ```sh
> git clone https://github.com/Wldc4rd/cairn-pack.git cairn
> ```

**Upgrading an existing cairn city** — additive, nothing breaks: pull the pack
(`git -C /path/to/cairn pull`), re-import (`gc import add /path/to/cairn`), and
you're done — memory behavior is unchanged (the full pre-0.2.0 test suite runs
unmodified), the `continuity` config block is absent-by-default, and no agent
sees any change until you opt them in. **Fresh install**: the Quick start above,
then return here.

**Turning continuity on — staged, in this order:**

1. Add the `continuity` block to `.gc/services/cairn/config.json` (copy from
   `examples/config.example.json`): set `enabled: true` and list ONE low-stakes
   agent in `enabled_agents`. Leave thresholds at 60/75/85 until you have local
   evidence.
2. `gc cairn continuity-init` — seeds that agent's notepad + diary (idempotent).
3. Wire the two host pieces in the settings file **your agent sessions actually
   load** (for gc-managed sessions that is the city's `.gc/settings.json`, not
   the repo's `.claude/settings.json` — verify with the session's `--settings`
   flag). Unwired, the gauge simply never appears and agents work as before.
4. **Verify before widening**: `gc cairn continuity-status` (notepad age, TTL
   tiers, diary, gauge heartbeat); confirm a real `context-gauge:` line in a
   live session's turn; expect the 4-line boot echo — *with the token* — in the
   agent's first report after its next restart.
5. Widen: **named / heartbeat-driven agents first** — every wake samples the
   gauge naturally, and long-lived sessions are where the anxiety disease
   actually lives. Hold single-burst ephemeral workers back (see limitations).

**Rollback** at any depth: delist the agent (or set `continuity.enabled:
false`) — the gauge falls silent, the skill section stops binding, and the
notepad/diary simply stop being read. Nothing else changes.

### Known limitations (read before fleet-wide)

- **Sampling is per prompt-event.** The gauge emits on `UserPromptSubmit`: idle
  wakes, nudges, and heartbeats all sample — but a single long agentic turn can
  burn 10→90% between samples, and **single-burst ephemeral workers get exactly
  one sample at t=0** (correctly silent — no usage exists yet). A throttled
  `PostToolUse` emitter is designed (see `POSTTOOLUSE-DESIGN-NOTE` in the repo
  history / bead trail) but not yet shipped — hence "named agents first."
- **No gauge line means UNKNOWN, never zero.** The skill says so; hold your
  agents to it. A dead hook degrades to the pre-continuity status quo, silently
  — check `continuity-status` for the gauge heartbeat when in doubt.
- **The window is only as true as its source.** Best: the statusline tee
  (ground truth per session). The fallbacks are labeled and honest, but
  `(assumed)` on a 1M-tier session reads ~5× hot — wire the tee.
- **Boot reads add startup weight.** On stores where session startup is already
  slow, the heavier boot can interact with aggressive supervisor stall-resets.
  If you see startup churn, that is a supervisor-tuning problem to fix first —
  continuity makes it more visible, not worse.
- **Hooks are provider-specific.** The shipped wiring targets Claude Code
  settings; other providers need equivalent adapter lines (same scripts, same
  stdin contract where available).

## Mounts: vault mode, repo mode, extra read mounts

Per-city config (`.gc/services/cairn/config.json`, see
`examples/config.example.json`):

- **`sync.mode: "vault"`** — the tree lives inside a vault whose own sync
  machinery commits. **Cairn never runs git in vault mode.**
- **`sync.mode: "repo"`** — the city's slice lives in a standalone git clone,
  synced by `gc cairn sync` through a manifest-guarded wrapper
  (`gc-brain.toml` fences every git call; `core.symlinks=false` at init).
- **`read_roots`** — extra read-only mounts merged into recall/search (e.g. a
  one-way mirror of the shared tree). Never point `write_root` at a mirror
  something else overwrites.

**Write ownership** (conflict-free by construction): each agent stacks only its
own cairn; city brains are written by that city; the nation brain is written by
the fleet steward — other cities' nation-scope writes are refused with a
"propose it" message unless `nation_write: "direct"`.

## Security

- **Default-off** everywhere: enable marker checked on every MCP call and CLI
  write; the recall order is triple-gated (above).
- **Secret-lint + banned-strings lint on every write** — credentials never
  belong on the cairn; leave a pointer, not the key. (`lint.banned_strings`
  ships empty; a deploying fleet sets its own list.)
- **Write confinement**: every path is containment-checked
  (`is_relative_to` + symlinked-ancestor refusal) before writing; keys and
  names are slug-sanitized before any path splice; symlinked note files are
  never read. Aligned with the bartertown/postman shared security core.
- Memory notes are your own fleet's content — no untrusted-content envelope on
  reads. If you ever mount another owner's brain, wrap it before surfacing.

## Migrating from `bd` memories

```sh
bd prime --export > /tmp/bd-export.txt
gc cairn migrate-bd --from-file /tmp/bd-export.txt --dry-run   # counts first
gc cairn migrate-bd --from-file /tmp/bd-export.txt
```

Every `### key` section lands as a city-scope stone (`migrated-from: bd`,
timestamps = migration time — bd stores none). Then curate: pin the standing
ones, retype in-flight ones as handoffs (`--type handoff --bead <id>`), promote
fleet-wide facts to nation, push agent-specific ones down, forget the dead.
The import is additive — leave the bd store untouched until you're satisfied.

## Renaming the pack

To rename: (1) rename the pack directory, `skills/cairn/`,
`mcp/cairn.template.toml`, and `orders/cairn-recall.toml` (+ its `exec` line);
(2) set `name` in `pack.toml`; (3) set `PACK_NAME` in
`scripts/memory_common.py` (env vars derive from it); (4) re-import. On-disk
note formats, the `gc-brain.toml` manifest, and the PRIME.md markers carry no
pack name and survive a rename untouched. (This recipe is load-tested: the pack
shipped as `obsidian` and became `cairn` with exactly these steps.)

## Layout

```
pack.toml                 mcp/cairn.template.toml        orders/cairn-recall.toml
scripts/memory_common.py  scripts/memory_admin.py        scripts/memory_mcp.py
scripts/continuity_common.py   scripts/context-gauge.py  scripts/statusline-tee.py
commands/<cmd>/{command.toml,run.sh}                     skills/cairn/SKILL.md
examples/config.example.json   tests/test_memory.py      tests/test_continuity.py
```

MIT licensed. Tests (hermetic — tmpdirs only):
`python3 -m unittest discover -s tests -v` (memory + continuity suites).

*The stones remember. Add yours before you go.*
