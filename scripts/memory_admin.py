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
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_common as mc  # noqa: E402


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

    args = ap.parse_args()
    try:
        return args.fn(args)
    except mc.MemoryError_ as e:
        print(f"{mc.PACK_NAME}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
