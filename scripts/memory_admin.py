#!/usr/bin/env python3
"""Memory admin CLI — backs the `gc cairn <cmd>` pack commands.

Subcommands:
  init         Write config + create this city's brain dirs (vault or repo mode)
  status       Config, roots, note counts per scope, enable/arm gates
  remember     Store/update a memory (default scope: the calling agent's brain)
  recall       Print the ranked, budgeted digest; --write-prime rewrites the
               marker region in .beads/PRIME.md (two-key gated; cutover act)
  search       Search the chain (--scope city spans sibling agent brains)
  forget       Delete a memory note (git history is the tombstone)
  migrate-bd   One-shot import of a `bd prime --export` dump into the city brain
  sync         Repo-mode: commit+pull+push the standalone brain repo
  enable/disable  Manage the .gc/<pack>.enabled marker (default OFF)
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_common as mc  # noqa: E402
import continuity_common as cc  # noqa: E402


def _city() -> Path:
    return mc.find_city_root()


def cmd_init(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    cfg.setdefault("city_name", args.city or mc.city_name(city, cfg))
    if args.write_root:
        cfg["write_root"] = args.write_root
    if args.read_root:
        roots = [r for r in (cfg.get("read_roots") or [])]
        for r in args.read_root:
            if r not in roots:
                roots.append(r)
        cfg["read_roots"] = roots
    if args.mode:
        cfg.setdefault("sync", {})["mode"] = args.mode
    if args.nation_write:
        cfg["nation_write"] = args.nation_write
    if "write_root" not in cfg:
        print("init needs --write-root (the base of the Gas Cities tree this city writes)",
              file=sys.stderr)
        return 2
    mc.save_config(city, cfg)
    cfg = mc.load_config(city)
    root = mc.write_root(city, cfg)
    cn = mc.city_name(city, cfg)
    if mc.sync_mode(cfg) == "repo":
        mc.repo_init(city, cfg, hub=args.hub)
    for scope in ("city",):
        d = mc.scope_dir(root, scope, cn, "shared")
        mc.confine_write(root, d)
        d.mkdir(parents=True, exist_ok=True)
    print(f"initialized {mc.PACK_NAME} memory: write_root={root} mode={mc.sync_mode(cfg)}")
    print(f"Pack remains DEFAULT-OFF until 'gc {mc.PACK_NAME} enable' (review first).")
    return 0


def cmd_status(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    info: dict = {
        "city": mc.city_name(city, cfg),
        "agent": mc.agent_name(),
        "enabled": mc.is_enabled(city),
        "recall_armed": mc.recall_armed(city),
        "sync_mode": mc.sync_mode(cfg),
        "budgets": mc.budgets(cfg),
    }
    try:
        info["write_root"] = str(mc.write_root(city, cfg))
        info["read_roots"] = [str(r) for r in mc.read_roots(city, cfg)]
        counts = {}
        for scope in mc.SCOPES:
            counts[scope] = len(mc.gather(city, cfg, scopes=(scope,)))
        info["notes"] = counts
    except mc.MemoryError_ as e:
        info["config_error"] = str(e)
    print(json.dumps(info, indent=2))
    return 0


def cmd_remember(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    body = args.body
    if body == "-":
        body = sys.stdin.read()
    path = mc.remember(city, cfg, args.key, body, scope=args.scope, type_=args.type,
                       pinned=args.pinned, tags=args.tag or [], bead=args.bead,
                       agent=args.agent or None)
    print(f"remembered [{args.key}] at {args.scope} scope: {path}")
    return 0


def cmd_recall(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    scopes = tuple(s for s in mc.SCOPES if s not in (args.exclude_scope or []))
    digest = mc.recall_digest(city, cfg, agent=args.agent or None, scopes=scopes,
                              budget_tokens=args.budget or None)
    if args.write_prime:
        # Cutover-gated: BOTH the pack master switch and the recall arm marker,
        # and the marker region must already exist in PRIME.md.
        mc.require_enabled(city)
        if not mc.recall_armed(city):
            print(f"recall not armed (operator: touch {mc.RECALL_ARM_MARKER}) — no-op", file=sys.stderr)
            return 0
        shared = mc.recall_digest(city, cfg, scopes=("city", "nation"),
                                  budget_tokens=args.budget or None)
        p = mc.write_prime_region(city, shared)
        print(f"wrote shared city+nation digest into {p}")
        return 0
    print(digest)
    return 0


def cmd_search(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    hits = mc.search(city, cfg, " ".join(args.query), agent=args.agent or None,
                     scope=args.scope, limit=args.limit)
    print(json.dumps({"query": " ".join(args.query), "scope": args.scope, "hits": hits},
                     indent=2, ensure_ascii=False))
    return 0


def cmd_forget(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    ok = mc.forget(city, cfg, args.key, scope=args.scope, agent=args.agent or None)
    print(f"forgot [{args.key}] ({args.scope})" if ok else f"no such memory [{args.key}] at {args.scope}")
    return 0 if ok else 1


def cmd_sync(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    mc.require_enabled(city)
    print(mc.repo_sync(city, cfg))
    return 0


_SECTION_RE = re.compile(r"^### (.+?)\s*$", re.M)


def cmd_migrate_bd(args) -> int:
    """Parse a `bd prime --export` dump: every `### <key>` section under the
    Persistent Memories heading becomes one city-scope note (bulk-then-curate)."""
    city = _city()
    cfg = mc.load_config(city)
    mc.require_enabled(city)
    text = Path(args.from_file).read_text() if args.from_file != "-" else sys.stdin.read()

    mem_start = text.find("## Persistent Memories")
    if mem_start < 0:
        print("no '## Persistent Memories' heading found in the export", file=sys.stderr)
        return 2
    tail = text[mem_start:]
    # memories run until the next H2 that is not the memories heading itself
    next_h2 = re.search(r"^## (?!Persistent Memories)", tail[3:], re.M)
    block = tail[: next_h2.start() + 3] if next_h2 else tail

    sections = _SECTION_RE.split(block)
    # split() yields [pre, key1, body1, key2, body2, ...]
    pairs = list(zip(sections[1::2], sections[2::2]))
    if not pairs:
        print("no ### sections found", file=sys.stderr)
        return 2

    ok, skipped, lint_flags = 0, [], []
    for raw_key, body in pairs:
        key = mc._sanitize_name(raw_key.strip())[:80]
        if not mc.valid_key(key):
            skipped.append(raw_key.strip()[:40])
            continue
        body = body.strip()
        # Import is deliberately non-blocking on lint (the content already lives
        # in bd with the same exposure) but every hit is surfaced for curation.
        hits = mc.secret_lint(body)
        if hits:
            lint_flags.append({"key": key, "matched": hits})
        if args.dry_run:
            ok += 1
            continue
        try:
            cn = mc.city_name(city, cfg)
            root = mc.write_root(city, cfg)
            with mc.tree_lock(city):
                d = mc.scope_dir(root, "city", cn, "shared")
                mc.confine_write(root, d)
                d.mkdir(parents=True, exist_ok=True)
                existing = mc.find_note(city, cfg, key, "city", "shared")
                now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                path = Path(existing["path"]) if existing else d / f"{now[:10]}-{key}.md"
                mc.confine_write(root, path)
                created = existing["created"] if existing else now
                path.write_text(mc.fm_render(
                    {"key": key, "type": "memory", "created": created, "updated": now,
                     "pinned": False, "migrated-from": "bd"}, body))
            ok += 1
        except mc.MemoryError_ as e:
            skipped.append(f"{key}: {e}")
    if not args.dry_run:
        root = mc.write_root(city, cfg)
        mc.regen_index(mc.scope_dir(root, "city", mc.city_name(city, cfg), "shared"), root)
        mc._repo_autosync(city, cfg)
    print(json.dumps({"migrated": ok, "skipped": skipped, "dry_run": bool(args.dry_run),
                      "secret_lint_flags": lint_flags}, indent=2))
    return 0


def cmd_enable(args) -> int:
    city = _city()
    marker = city / mc.ENABLE_MARKER
    if marker.exists():
        print("already enabled")
        return 0
    if not args.reviewed:
        print("REFUSING: enabling the memory pack is a go-live action.\n"
              "Review the pack, then re-run with --reviewed-by <your-mayor>.", file=sys.stderr)
        return 3
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f"enabled {dt.datetime.now(dt.timezone.utc).isoformat()} "
                      f"reviewed-by={args.reviewed}\n")
    print(f"enabled ({mc.ENABLE_MARKER})")
    return 0


def cmd_disable(args) -> int:
    city = _city()
    marker = city / mc.ENABLE_MARKER
    if marker.exists():
        marker.unlink()
        print("disabled")
    else:
        print("already disabled")
    return 0


# ---------------------------------------------------------------------------
# Continuity layer commands (gauge companion; default-OFF behind the enable gate)
# ---------------------------------------------------------------------------

_NOTEPAD_TEMPLATE = """\
---
key: notepad-{agent}
type: handoff
created: {stamp}
updated: {stamp}
pinned: true
tags: notepad, handoff, wal, continuity
---

echo-token: {token}

# NOTEPAD — {agent} (working-memory head)

> **Contract:** full rules live in your prompt's *Continuity Protocol* section — this
> header is only the read-time reminders. This file is a SNAPSHOT (every line currently
> true; order != time; stamp bullets `(M/D HHMMZ)` UTC at bullet start). It carries ONLY
> what the issue tracker cannot: settled (with provenance+scope), gotchas, the why, next.
> At boot: read this IN FULL + the latest terminated diary entry, then give the 4-line
> boot echo INCLUDING the echo-token above. At handoff: re-affirm-or-drop every line
> (cite the check), rotate the echo-token, diary entry <=30 lines ending `{terminator}`.

## In flight
<!-- what you're mid-way through + the next concrete step; newest-first; stamp each bullet -->

## Settled — do NOT re-open or re-litigate
<!-- only with provenance: who ruled it, when, quote/bead, and scope ("tonight" != "forever") -->

## Waiting on
<!-- external blockers; newest-first; stamp each bullet -->

## Gotchas / active constraints
<!-- hard-won constraints and the why; these age slower than in-flight lines -->
"""


def _rotate_notepad(text: str, new_tok: str, stamp: str):
    """Return (new_text, token_rotated, stamp_refreshed)."""
    text, n_tok = re.subn(r"^echo-token:\s*\S+", f"echo-token: {new_tok}",
                          text, count=1, flags=re.MULTILINE)
    text, n_stamp = re.subn(r"^updated:\s*.*$", f"updated: {stamp}",
                            text, count=1, flags=re.MULTILINE)
    return text, bool(n_tok), bool(n_stamp)


def cmd_continuity_init(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    mc.require_enabled(city)
    ccfg = cc.continuity_cfg(cfg)
    agents = cc.target_agents(ccfg, args.agent or None)
    root = mc.write_root(city, cfg)
    print(f"continuity-init{' [DRY-RUN]' if args.dry_run else ''}")
    for agent in agents:
        brain = cc.brain_dir(city, cfg, agent)
        np, dd = cc.notepad_path(brain), cc.diary_dir(brain)
        print(f"  agent {agent}: brain {brain}")
        if dd.is_dir():
            print(f"    - diary/ exists ({len(cc.diary_entries(dd))} entries) — kept")
        else:
            print("    - diary/ CREATE")
            if not args.dry_run:
                mc.confine_write(root, dd)
                dd.mkdir(parents=True, exist_ok=True)
        if np.is_file():
            print(f"    - notepad.md exists (echo-token {cc.read_echo_token(np) or '?'}) — kept")
        else:
            tok = cc.new_token()
            print(f"    - notepad.md CREATE (echo-token {tok})")
            if not args.dry_run:
                brain.mkdir(parents=True, exist_ok=True)
                mc.confine_write(root, np)
                np.write_text(_NOTEPAD_TEMPLATE.format(
                    agent=agent, stamp=cc.utc_now().strftime("%Y-%m-%dT%H%M%SZ"),
                    token=tok, terminator=cc.TERMINATOR))
    return 0


def cmd_continuity_handoff(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    mc.require_enabled(city)
    ccfg = cc.continuity_cfg(cfg)
    agent = args.agent or cc.resolve_agent(cc.env_identity(), cc.enabled_agents(ccfg))
    if not agent:
        print("continuity-handoff: cannot resolve agent from env "
              "(CAIRN_AGENT/GC_ALIAS) against enabled_agents; pass --agent", file=sys.stderr)
        return 2
    brain = cc.brain_dir(city, cfg, agent)
    np, dd = cc.notepad_path(brain), cc.diary_dir(brain)

    latest = cc.latest_diary(dd)
    term_ok = False
    if latest is None:
        msg = f"no diary entry in {dd} — write one (with terminator) first"
        if not args.force:
            print(f"continuity-handoff: {msg}", file=sys.stderr)
            return 3
        print(f"continuity-handoff: WARNING {msg} (--force)")
    else:
        term_ok = cc.has_terminator(dd / latest)
        if not term_ok:
            msg = (f"latest diary {latest} has NO terminator line "
                   f"('{cc.TERMINATOR}') — finish the entry first")
            if not args.force:
                print(f"continuity-handoff: {msg}", file=sys.stderr)
                return 3
            print(f"continuity-handoff: WARNING {msg} (--force)")

    now = cc.utc_now()
    stamp = now.strftime("%Y-%m-%dT%H%M%SZ")
    new_tok = cc.new_token()
    old_tok = cc.read_echo_token(np)
    try:
        text = np.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"continuity-handoff: cannot read notepad {np}: {e}", file=sys.stderr)
        return 2
    new_text, rot, stamped = _rotate_notepad(text, new_tok, stamp)

    bead = args.bead or "(unset — pass --bead)"
    body = f"notepad: {np} | diary: {latest or '(none)'} | bead: {bead}"
    subject = args.subject or f"{agent} handoff — {bead}"
    diary_label = "(none)" if latest is None else (
        f"{latest} [{'terminator OK' if term_ok else 'NO TERMINATOR (--force override)'}]")

    print(f"continuity-handoff{' [DRY-RUN]' if args.dry_run else ''} — agent {agent}")
    print(f"  diary latest : {diary_label}")
    print(f"  echo-token   : {old_tok} -> {new_tok}"
          f"{'' if rot else '  (WARN: no echo-token line to rotate)'}")
    print(f"  updated stamp: {'refreshed -> ' + stamp if stamped else '(no updated: line)'}")
    print(f"  pointer body : {body}")

    if args.dry_run:
        print(f"  gc handoff   : gc handoff {subject!r} {body!r}")
        print("  DRY-RUN: notepad NOT written, gc handoff NOT executed.")
        return 0

    mc.confine_write(mc.write_root(city, cfg), np)
    tmp = np.with_suffix(".md.tmp")
    tmp.write_text(new_text)
    os.replace(tmp, np)
    print("  notepad rotation committed. Executing gc handoff...")
    return subprocess.run(["gc", "handoff", subject, body]).returncode


def cmd_continuity_status(args) -> int:
    city = _city()
    cfg = mc.load_config(city)
    ccfg = cc.continuity_cfg(cfg)
    agents = cc.target_agents(ccfg, args.agent or None)
    ttl = ccfg["ttl"]
    now = cc.utc_now()
    info: dict = {
        "pack_enabled": mc.is_enabled(city),
        "continuity_enabled": bool(ccfg.get("enabled")),
        "enabled_agents": cc.enabled_agents(ccfg),
        "agents": {},
    }
    for agent in agents:
        a: dict = {}
        try:
            brain = cc.brain_dir(city, cfg, agent)
            a["brain"] = str(brain)
            np, dd = cc.notepad_path(brain), cc.diary_dir(brain)
            if np.is_file():
                scan = cc.notepad_scan(np, now, ttl)
                a["notepad"] = {
                    "echo_token": cc.read_echo_token(np),
                    "newest_stamp_age_min": (int((now - scan["newest"]).total_seconds() / 60)
                                             if scan.get("newest") else None),
                    "suspect": scan.get("suspect"),
                    "reassess": scan.get("reassess"),
                    "unstamped": scan.get("unstamped"),
                }
            else:
                a["notepad"] = None
            entries = cc.diary_entries(dd)
            a["diary"] = {
                "count": len(entries),
                "latest": entries[-1] if entries else None,
                "latest_terminator": cc.has_terminator(dd / entries[-1]) if entries else None,
            }
            st = cc.state_path(city, ccfg, agent)
            if st.is_file():
                try:
                    sd = json.loads(st.read_text())
                except (OSError, json.JSONDecodeError):
                    sd = {}
                at = float(sd.get("at") or 0)
                a["gauge_heartbeat_sec"] = int(time.time() - at) if at > 0 else None
                a["consecutive_urgent"] = sd.get("consecutive_urgent")
            else:
                a["gauge_heartbeat_sec"] = None
        except mc.MemoryError_ as e:
            a["error"] = str(e)
        info["agents"][agent] = a
    print(json.dumps(info, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog=mc.PACK_NAME)
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("init"); p.set_defaults(fn=cmd_init)
    p.add_argument("--city", default="")
    p.add_argument("--write-root", default="", help="base of the Gas Cities tree this city writes")
    p.add_argument("--read-root", action="append", default=[],
                   help="extra read-only Gas Cities mount (repeatable; e.g. a vault slice)")
    p.add_argument("--mode", choices=["vault", "repo"], default="",
                   help="vault: the vault's own sync commits (no git here); repo: standalone clone")
    p.add_argument("--hub", default="", help="repo mode: git URL of the brain hub")
    p.add_argument("--nation-write", choices=["propose", "direct"], default="")

    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("remember"); p.set_defaults(fn=cmd_remember)
    p.add_argument("key"); p.add_argument("body", help="'-' reads stdin")
    p.add_argument("--scope", choices=list(mc.SCOPES), default="agent")
    p.add_argument("--type", choices=["memory", "handoff"], default="memory")
    p.add_argument("--pinned", action="store_true")
    p.add_argument("--tag", action="append", default=[])
    p.add_argument("--bead", default="")
    p.add_argument("--agent", default="")

    p = sub.add_parser("recall"); p.set_defaults(fn=cmd_recall)
    p.add_argument("--agent", default="")
    p.add_argument("--budget", type=int, default=0)
    p.add_argument("--exclude-scope", action="append", default=[])
    p.add_argument("--write-prime", action="store_true",
                   help="rewrite the marker region in .beads/PRIME.md with the shared "
                        "city+nation band (two-key gated)")

    p = sub.add_parser("search"); p.set_defaults(fn=cmd_search)
    p.add_argument("query", nargs="+")
    p.add_argument("--scope", choices=["chain", "city", "nation"], default="chain")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--agent", default="")

    p = sub.add_parser("forget"); p.set_defaults(fn=cmd_forget)
    p.add_argument("key")
    p.add_argument("--scope", choices=list(mc.SCOPES), default="agent")
    p.add_argument("--agent", default="")

    sub.add_parser("sync").set_defaults(fn=cmd_sync)

    p = sub.add_parser("migrate-bd"); p.set_defaults(fn=cmd_migrate_bd)
    p.add_argument("--from-file", required=True, help="path to a `bd prime --export` dump ('-' = stdin)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("enable"); p.set_defaults(fn=cmd_enable)
    p.add_argument("--reviewed-by", dest="reviewed", default="")

    sub.add_parser("disable").set_defaults(fn=cmd_disable)

    p = sub.add_parser("continuity-init"); p.set_defaults(fn=cmd_continuity_init)
    p.add_argument("--agent", default="", help="single agent (default: all enabled_agents)")
    p.add_argument("--dry-run", action="store_true", help="show actions, write nothing")

    p = sub.add_parser("continuity-handoff"); p.set_defaults(fn=cmd_continuity_handoff)
    p.add_argument("--bead", default="", help="bead/issue id to point the successor at")
    p.add_argument("--subject", default="", help="handoff mail subject (default derived)")
    p.add_argument("--agent", default="", help="agent (default: resolved from env identity)")
    p.add_argument("--dry-run", action="store_true",
                   help="verify + print the plan; write nothing, exec nothing")
    p.add_argument("--force", action="store_true",
                   help="proceed even if the latest diary lacks a terminator")

    p = sub.add_parser("continuity-status"); p.set_defaults(fn=cmd_continuity_status)
    p.add_argument("--agent", default="", help="single agent (default: all enabled_agents)")

    args = ap.parse_args()
    try:
        return args.fn(args)
    except mc.MemoryError_ as e:
        print(f"{mc.PACK_NAME}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
