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
commands/<cmd>/{command.toml,run.sh}                     skills/cairn/SKILL.md
examples/config.example.json                             tests/test_memory.py
```

MIT licensed. Tests: `python3 tests/test_memory.py -v` (hermetic — tmpdirs only).

*The stones remember. Add yours before you go.*
