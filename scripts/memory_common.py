#!/usr/bin/env python3
"""Memory common — vault-backed, scope-chained agent memory for Gas City.

One memory = one markdown note with dated/typed frontmatter, living in a
``brain/`` folder at one of three scopes:

    <root>/brain/                          NATION  — fleet-wide knowledge
    <root>/<city>/brain/                   CITY    — shared by this city's agents
    <root>/<city>/agents/<agent>/brain/    AGENT   — one agent's own memory

Recall walks the chain agent → city → nation (most-specific-first, key-dedup),
ranks pinned → open handoffs → newest-updated, and renders a token-budgeted
digest — the session-start head that replaces an unbounded memory dump
(truncation impossible by construction). Any folder of markdown is an Obsidian
vault, so the store is timestamped, searchable, versioned, and human-editable
for free.

Two mounts, one tree (per-city config):
  - sync.mode = "vault": the tree lives inside an owner vault whose own sync
    machinery commits — THIS MODULE NEVER RUNS GIT in vault mode.
  - sync.mode = "repo": the city's slice lives in a dedicated git clone synced
    through the manifest-guarded wrapper below (transport aligned with the
    bartertown/postman shared security core, incl. the clone-confinement
    hardening: path containment, symlink refusal, core.symlinks=false, slug ids).

SECURITY CORE (aligned with bartertown/postman — a fix lands once):
confinement on every write (is_relative_to + no symlinked ancestor), symlink
refusal on reads, slug-sanitized keys/names before any path splice, secret-lint
+ banned-strings lint on every remember. Secrets never belong in memory notes —
store a pointer, never the credential.

Stdlib only. Final name "Cairn" (rename recipe: pack dir + pack.toml name +
PACK_NAME below (env vars derive from it); file formats carry no pack name.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import subprocess
import time
from pathlib import Path

PACK_NAME = "cairn"  # final name: the waymarker — every traveler adds a stone

ENABLE_MARKER = f".gc/{PACK_NAME}.enabled"
RECALL_ARM_MARKER = f".gc/{PACK_NAME}-recall.enabled"
SERVICE_DIR = f".gc/services/{PACK_NAME}"
ENV_PREFIX = PACK_NAME.upper()  # CAIRN_CITY_ROOT / CAIRN_AGENT

# Rename-proof on-disk names (never derived from PACK_NAME):
REPO_MANIFEST = "gc-brain.toml"           # marks a repo-mode clone; git is fenced to it
PRIME_BEGIN = "<!-- brain-recall:begin -->"
PRIME_END = "<!-- brain-recall:end -->"

SCHEMA_VERSION = 1
SCOPES = ("agent", "city", "nation")

DISABLED_MSG = (
    f"{PACK_NAME} memory is disabled on this city. Review the pack, then run "
    f"'gc {PACK_NAME} enable --reviewed-by <your-mayor>'."
)


class MemoryError_(Exception):
    pass


# ---------------------------------------------------------------------------
# City / identity / config
# ---------------------------------------------------------------------------

def find_city_root(start: str | None = None) -> Path:
    env = os.environ.get(f"{ENV_PREFIX}_CITY_ROOT", "").strip()
    if env:
        p = Path(env).expanduser()
        if (p / "city.toml").is_file():
            return p
        raise MemoryError_(f"{ENV_PREFIX}_CITY_ROOT={env} has no city.toml")
    cur = Path(start or os.getcwd()).resolve()
    for cand in [cur, *cur.parents]:
        if (cand / "city.toml").is_file():
            return cand
    raise MemoryError_(f"no Gas City root found (city.toml) from cwd; set {ENV_PREFIX}_CITY_ROOT")


def service_root(city: Path) -> Path:
    return city / SERVICE_DIR


def data_dir(city: Path) -> Path:
    return service_root(city) / "data"


def config_path(city: Path) -> Path:
    return service_root(city) / "config.json"


def is_enabled(city: Path) -> bool:
    return (city / ENABLE_MARKER).is_file()


def require_enabled(city: Path) -> None:
    if not is_enabled(city):
        raise MemoryError_(DISABLED_MSG)


def recall_armed(city: Path) -> bool:
    return (city / RECALL_ARM_MARKER).is_file()


def load_config(city: Path) -> dict:
    p = config_path(city)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise MemoryError_(f"unreadable {p}: {e}")


def save_config(city: Path, cfg: dict) -> None:
    p = config_path(city)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, p)


def _sanitize_name(name: str) -> str:
    out = re.sub(r"[^a-z0-9-]+", "-", (name or "").lower()).strip("-")
    return out or "unnamed"


def city_name(city: Path, cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else load_config(city)
    return _sanitize_name(str(cfg.get("city_name", "")).strip() or city.name)


def agent_name() -> str:
    for var in (f"{ENV_PREFIX}_AGENT", "GC_ALIAS", "GC_AGENT"):
        v = os.environ.get(var, "").strip()
        if v:
            return _sanitize_name(v)
    return "shared"


# ---------------------------------------------------------------------------
# Roots + scope dirs
# ---------------------------------------------------------------------------

def write_root(city: Path, cfg: dict | None = None) -> Path:
    cfg = cfg if cfg is not None else load_config(city)
    raw = str(cfg.get("write_root", "")).strip()
    if not raw:
        raise MemoryError_(
            f"config has no write_root; run 'gc {PACK_NAME} init' (see examples/config.example.json)")
    return Path(raw).expanduser()


def read_roots(city: Path, cfg: dict | None = None) -> list[Path]:
    """All roots visible for READS: the write root first, then extra read-only
    mounts (e.g. a one-way vault slice on a box without a writable vault)."""
    cfg = cfg if cfg is not None else load_config(city)
    roots = [write_root(city, cfg)]
    for raw in cfg.get("read_roots") or []:
        p = Path(str(raw)).expanduser()
        if p not in roots:
            roots.append(p)
    return roots


def scope_dir(root: Path, scope: str, cn: str, agent: str) -> Path:
    if scope == "nation":
        return root / "brain"
    if scope == "city":
        return root / cn / "brain"
    if scope == "agent":
        return root / cn / "agents" / _sanitize_name(agent) / "brain"
    raise MemoryError_(f"unknown scope: {scope}")


# ---------------------------------------------------------------------------
# Write confinement (aligned with bartertown/postman hs-i754z)
# ---------------------------------------------------------------------------

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def valid_key(value: str) -> bool:
    return bool(_KEY_RE.match(value or ""))


def require_key(value: str) -> str:
    if not valid_key(value):
        raise MemoryError_(f"invalid memory key (must match {_KEY_RE.pattern}): {str(value)[:80]!r}")
    return value


def _within(base: Path, target: Path) -> bool:
    try:
        return target.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


def confine_write(base: Path, target: Path) -> Path:
    """Assert a write target resolves inside base and is reached through no
    symlink below base. Raises on any escape."""
    base_r = base.resolve()
    for anc in [target, *target.parents]:
        if anc == base_r or anc == base:
            break
        if anc.is_symlink():
            raise MemoryError_(f"refusing symlinked path in memory tree: {anc}")
    if not _within(base, target):
        raise MemoryError_(f"refusing write outside memory root: {target}")
    return target


class tree_lock:
    """flock serializing writes across processes sharing one city's memory."""

    def __init__(self, city: Path):
        d = data_dir(city)
        d.mkdir(parents=True, exist_ok=True)
        self._path = d / "tree.lock"

    def __enter__(self):
        self._fh = open(self._path, "w")
        fcntl.flock(self._fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self._fh, fcntl.LOCK_UN)
        self._fh.close()
        return False


# ---------------------------------------------------------------------------
# Frontmatter (tolerant parse — human-edited notes must never crash a reader)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fm_render(meta: dict, body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        elif isinstance(v, bool):
            v = "true" if v else "false"
        v = str(v).replace("\n", " ")
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.rstrip() + "\n"


def fm_parse(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 2)
    if len(parts) < 2:
        return {}, text
    head = parts[0][3:]
    body = parts[1]
    if body.startswith("\n"):
        body = body[1:]
    meta: dict = {}
    for line in head.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        meta[k.strip()] = v.strip()
    return meta, body.lstrip("\n")


def _parse_ts(val: str) -> float:
    try:
        return dt.datetime.fromisoformat(str(val).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


# ---------------------------------------------------------------------------
# Notes — load / write / forget
# ---------------------------------------------------------------------------

def _note_from_file(path: Path, scope: str, root: Path) -> dict | None:
    """Parse one note file. Symlinks and escapes are never read (untrusted
    peers exist in repo mode; humans mis-drop files in vault mode)."""
    if path.is_symlink() or not _within(root, path):
        return None
    if not path.is_file() or path.name.startswith("_") or not path.name.endswith(".md"):
        return None
    try:
        meta, body = fm_parse(path.read_text(errors="replace"))
    except OSError:
        return None
    key = str(meta.get("key", "")).strip()
    if not valid_key(key):
        # fall back to a slug from the filename (drop a leading YYYY-MM-DD-)
        stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", path.stem)
        key = _sanitize_name(stem)[:80]
        if not valid_key(key):
            return None
    return {
        "key": key,
        "type": (str(meta.get("type", "memory")).strip().lower() or "memory"),
        "pinned": str(meta.get("pinned", "")).strip().lower() in ("true", "yes", "1"),
        "bead": str(meta.get("bead", "")).strip(),
        "tags": [t.strip() for t in str(meta.get("tags", "")).split(",") if t.strip()],
        "created": str(meta.get("created", "")).strip(),
        "updated": str(meta.get("updated", meta.get("created", ""))).strip(),
        "updated_ts": _parse_ts(meta.get("updated", meta.get("created", ""))) or path.stat().st_mtime,
        "body": body,
        "path": str(path),
        "scope": scope,
    }


def scan_scope(root: Path, scope: str, cn: str, agent: str) -> list[dict]:
    d = scope_dir(root, scope, cn, agent)
    if not d.is_dir():
        return []
    notes = []
    for p in sorted(d.iterdir()):
        n = _note_from_file(p, scope, root)
        if n:
            notes.append(n)
    return notes


def find_note(city: Path, cfg: dict, key: str, scope: str, agent: str) -> dict | None:
    cn = city_name(city, cfg)
    for n in scan_scope(write_root(city, cfg), scope, cn, agent):
        if n["key"] == key:
            return n
    return None


def remember(city: Path, cfg: dict, key: str, body: str, *, scope: str = "agent",
             type_: str = "memory", pinned: bool = False, tags: list[str] | None = None,
             bead: str = "", agent: str | None = None) -> Path:
    """Create or update-in-place (same key + scope). Lints first; confined write."""
    require_enabled(city)
    agent = _sanitize_name(agent or agent_name())
    require_key(key)
    if scope not in SCOPES:
        raise MemoryError_(f"scope must be one of {SCOPES}: {scope!r}")
    if type_ not in ("memory", "handoff"):
        raise MemoryError_(f"type must be memory|handoff: {type_!r}")

    hits = secret_lint("\n".join([key, body or "", " ".join(tags or [])]))
    if hits:
        raise MemoryError_(
            "memory rejected by secret lint (matched: " + ", ".join(hits) +
            "). Credentials never enter memory notes; store a pointer, not the secret.")
    bhits = banned_strings_lint("\n".join([key, body or ""]), cfg)
    if bhits:
        raise MemoryError_("memory rejected by banned-strings lint (matched: " + ", ".join(bhits) + ")")

    budgets_ = budgets(cfg)
    if len((body or "").encode("utf-8")) > int(budgets_["max_note_bytes"]):
        raise MemoryError_(f"note body exceeds max_note_bytes ({budgets_['max_note_bytes']})")

    if scope == "nation" and str(cfg.get("nation_write", "propose")).strip() != "direct":
        raise MemoryError_(
            "nation-scope writes are propose-only on this city: send the memory to your "
            "fleet steward to promote (config nation_write=direct to allow).")

    cn = city_name(city, cfg)
    root = write_root(city, cfg)
    with tree_lock(city):
        existing = find_note(city, cfg, key, scope, agent)
        d = scope_dir(root, scope, cn, agent)
        confine_write(root, d)
        d.mkdir(parents=True, exist_ok=True)
        now = _now_iso()
        if existing:
            path = Path(existing["path"])
            created = existing["created"] or now
        else:
            path = d / f"{now[:10]}-{key}.md"
            created = now
        confine_write(root, path)
        meta = {
            "key": key, "type": type_, "created": created, "updated": now,
            "pinned": pinned,
        }
        if bead:
            meta["bead"] = _sanitize_name(bead)
        if tags:
            meta["tags"] = [_sanitize_name(t) for t in tags]
        path.write_text(fm_render(meta, body or ""))
        regen_index(d, root)
    _repo_autosync(city, cfg)
    return path


def forget(city: Path, cfg: dict, key: str, *, scope: str = "agent",
           agent: str | None = None) -> bool:
    """Delete the note file (git history is the tombstone)."""
    require_enabled(city)
    agent = _sanitize_name(agent or agent_name())
    require_key(key)
    root = write_root(city, cfg)
    with tree_lock(city):
        n = find_note(city, cfg, key, scope, agent)
        if not n:
            return False
        p = Path(n["path"])
        confine_write(root, p)
        p.unlink()
        regen_index(p.parent, root)
    _repo_autosync(city, cfg)
    return True


def regen_index(brain_dir: Path, root: Path) -> None:
    """_index.md: newest-first human listing (Obsidian-friendly)."""
    if not brain_dir.is_dir():
        return
    notes = []
    for p in brain_dir.iterdir():
        n = _note_from_file(p, "any", root)
        if n:
            notes.append(n)
    notes.sort(key=lambda n: -n["updated_ts"])
    lines = ["# Brain index (generated — newest first)", ""]
    for n in notes:
        flags = "".join([" 📌" if n["pinned"] else "", " 🤝" if n["type"] == "handoff" else ""])
        lines.append(f"- [[{Path(n['path']).stem}]]{flags} — upd {n['updated'][:10]}")
    idx = brain_dir / "_index.md"
    confine_write(root, idx)
    idx.write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------------------
# Recall — gather chain, dedup, rank, budget, render
# ---------------------------------------------------------------------------

def _bead_open(city: Path, bead: str, cache: dict) -> bool:
    """Is this bead still open? Unknown/error => True (safer to surface)."""
    if not bead:
        return False
    if bead in cache:
        return cache[bead]
    open_ = True
    try:
        proc = subprocess.run(["bd", "show", bead, "--json"], capture_output=True,
                              text=True, timeout=10, cwd=str(city))
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            item = data[0] if isinstance(data, list) and data else data
            open_ = str(item.get("status", "")).lower() not in ("closed", "deferred")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        open_ = True
    cache[bead] = open_
    return open_


def gather(city: Path, cfg: dict, *, agent: str | None = None,
           scopes: tuple = SCOPES) -> list[dict]:
    """Merged chain, most-specific-first with key-dedup: an agent-scope memory
    shadows a same-key city/nation one. Root order breaks ties within a scope."""
    agent = _sanitize_name(agent or agent_name())
    cn = city_name(city, cfg)
    seen: set[str] = set()
    merged: list[dict] = []
    for scope in scopes:
        for root in read_roots(city, cfg):
            for n in scan_scope(root, scope, cn, agent):
                if n["key"] in seen:
                    continue
                seen.add(n["key"])
                merged.append(n)
    return merged


def rank(city: Path, notes: list[dict]) -> list[dict]:
    cache: dict = {}
    for n in notes:
        n["open_handoff"] = n["type"] == "handoff" and _bead_open(city, n["bead"], cache)
    return sorted(notes, key=lambda n: (not n["pinned"], not n["open_handoff"], -n["updated_ts"]))


def _est_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def render_digest(city: Path, cfg: dict, notes: list[dict], *, budget_tokens: int | None = None,
                  title: str = "Brain recall") -> str:
    b = budgets(cfg)
    budget = int(budget_tokens or b["digest_token_budget"])
    cap = int(b["entry_char_cap"])
    out: list[str] = [f"## {title} (generated {_now_iso()}; ranked pinned → open-handoff → newest)"]
    spent = _est_tokens(out[0])
    shown = 0
    for n in notes:
        body = n["body"].strip()
        clipped = ""
        if len(body) > cap:
            body = body[:cap]
            clipped = f"\n_(clipped — `gc {PACK_NAME} search {n['key']}` for the rest)_"
        flags = []
        if n["pinned"]:
            flags.append("pinned")
        if n.get("open_handoff"):
            flags.append("OPEN HANDOFF")
        elif n["type"] == "handoff":
            flags.append("handoff/closed")
        head = f"### {n['key']}  ·  {n['scope']}{' · ' + ', '.join(flags) if flags else ''} · upd {n['updated'][:10]}"
        entry = f"\n{head}\n{body}{clipped}\n"
        cost = _est_tokens(entry)
        if spent + cost > budget:
            break
        out.append(entry)
        spent += cost
        shown += 1
    rest = len(notes) - shown
    if rest > 0:
        out.append(f"\n_{rest} more memor{'y' if rest == 1 else 'ies'} not shown — "
                   f"`gc {PACK_NAME} search <keyword>` or the memory_search tool._")
    return "\n".join(out).rstrip() + "\n"


def recall_digest(city: Path, cfg: dict, *, agent: str | None = None,
                  scopes: tuple = SCOPES, budget_tokens: int | None = None,
                  title: str | None = None) -> str:
    require_enabled(city)
    notes = rank(city, gather(city, cfg, agent=agent, scopes=scopes))
    ttl = title or (f"Brain recall — {city_name(city, cfg)}"
                    + ("" if "agent" in scopes else " (shared city+nation band; run "
                       f"`gc {PACK_NAME} recall` for your personalized agent→city→nation chain)"))
    return render_digest(city, cfg, notes, budget_tokens=budget_tokens, title=ttl)


def write_prime_region(city: Path, digest: str, prime_path: Path | None = None) -> Path:
    """Rewrite the marker-fenced region of .beads/PRIME.md. Refuses when the
    markers are absent — adding them is the (gated) cutover act, not ours."""
    p = prime_path or (city / ".beads" / "PRIME.md")
    if not p.is_file():
        raise MemoryError_(f"{p} not found — cutover not prepared")
    text = p.read_text()
    if PRIME_BEGIN not in text or PRIME_END not in text:
        raise MemoryError_(
            f"{p} has no {PRIME_BEGIN} … {PRIME_END} region — add the markers at cutover first")
    pre, rest = text.split(PRIME_BEGIN, 1)
    _, post = rest.split(PRIME_END, 1)
    p.write_text(pre + PRIME_BEGIN + "\n" + digest.rstrip() + "\n" + PRIME_END + post)
    return p


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(city: Path, cfg: dict, query: str, *, agent: str | None = None,
           scope: str = "chain", limit: int = 20) -> list[dict]:
    """scope: chain = self+ancestors (default) · city = chain + SIBLING agent
    brains (a missed promotion is discoverable, not siloed) · nation = every
    brain mounted locally."""
    require_enabled(city)
    agent = _sanitize_name(agent or agent_name())
    cn = city_name(city, cfg)
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        raise MemoryError_("query is required")

    dirs: list[tuple[str, Path]] = []
    roots = read_roots(city, cfg)
    for r in roots:
        for sc in SCOPES:
            dirs.append((sc, scope_dir(r, sc, cn, agent)))
        if scope in ("city", "nation"):
            agents_dir = r / cn / "agents"
            if agents_dir.is_dir():
                for a in sorted(agents_dir.iterdir()):
                    if a.is_dir() and not a.is_symlink() and _sanitize_name(a.name) != agent:
                        dirs.append((f"agent:{_sanitize_name(a.name)}", a / "brain"))
        if scope == "nation":
            for c in sorted(r.iterdir()) if r.is_dir() else []:
                if c.is_dir() and not c.is_symlink() and c.name not in ("brain", cn):
                    dirs.append((f"city:{_sanitize_name(c.name)}", c / "brain"))

    seen_paths: set = set()
    hits: list[dict] = []
    for label, d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            if str(p) in seen_paths:
                continue
            seen_paths.add(str(p))
            root = next((r for r in roots if _within(r, p)), None)
            n = _note_from_file(p, label, root or d)
            if not n:
                continue
            hay = "\n".join([n["key"], " ".join(n["tags"]), n["body"]]).lower()
            # AND-of-terms: every term must appear somewhere in the note
            if not all(t in hay for t in terms):
                continue
            i = max(hay.find(terms[0]) - len(n["key"]) - 1, 0)
            snip = n["body"][max(0, i - 40):i + 160].replace("\n", " ").strip()
            hits.append({"key": n["key"], "scope": label, "updated": n["updated"],
                         "updated_ts": n["updated_ts"], "pinned": n["pinned"],
                         "type": n["type"], "snippet": snip, "path": n["path"]})
    hits.sort(key=lambda h: (-h["pinned"], -h["updated_ts"]))
    return hits[: max(1, int(limit))]


# ---------------------------------------------------------------------------
# Lint (aligned with bartertown/postman)
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("AWS access key id", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b")),
    ("JWT / long-lived token", re.compile(r"\beyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{10,}\b")),
    ("Discord bot token", re.compile(r"\b[MNO][A-Za-z\d_-]{23,}\.[A-Za-z\d_-]{6}\.[A-Za-z\d_-]{27,}\b")),
    ("bearer credential", re.compile(r"(?i)\bauthorization:\s*bearer\s+[A-Za-z0-9._~+/=-]{16,}")),
    ("credential assignment", re.compile(
        r"(?i)\b(password|passwd|api[_-]?key|secret|token|access[_-]?key)\s*[:=]\s*['\"]?[^\s'\"]{12,}")),
    ("ssh private key path leak", re.compile(r"(?i)BEGIN OPENSSH PRIVATE KEY")),
    ("high-entropy hex blob", re.compile(r"\b[0-9a-fA-F]{48,}\b")),
]


def secret_lint(text: str) -> list[str]:
    return [name for name, pat in _SECRET_PATTERNS if pat.search(text or "")]


_DEFAULT_BANNED_STRINGS: list[str] = []  # public default; a deploying city sets its own


def banned_strings_lint(text: str, cfg: dict) -> list[str]:
    lint = cfg.get("lint") or {}
    vals = lint.get("banned_strings")
    if vals is None:
        vals = _DEFAULT_BANNED_STRINGS
    low = (text or "").lower()
    return [f"banned string ({s})" for s in
            [str(v).strip().lower() for v in vals if str(v).strip()] if s in low]


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

_DEFAULT_BUDGETS = {
    "digest_token_budget": 7000,   # ~6-8K per the design review; configurable
    "entry_char_cap": 1200,
    "max_note_bytes": 16384,
}


def budgets(cfg: dict) -> dict:
    b = dict(_DEFAULT_BUDGETS)
    b.update(cfg.get("budgets") or {})
    return b


# ---------------------------------------------------------------------------
# Repo-mode sync — manifest-guarded git, ONLY for sync.mode == "repo".
# Vault mode never touches git (the vault's own sync commits).
# ---------------------------------------------------------------------------

def sync_mode(cfg: dict) -> str:
    return str((cfg.get("sync") or {}).get("mode", "vault")).strip() or "vault"


def _assert_brain_repo(repo: Path) -> None:
    if not (repo / REPO_MANIFEST).is_file():
        raise MemoryError_(f"refusing git operation: {repo} has no {REPO_MANIFEST} (not a brain repo)")


def git(repo: Path, args: list[str], check: bool = True, allow_missing_manifest: bool = False,
        timeout: int = 120) -> subprocess.CompletedProcess:
    if not allow_missing_manifest:
        _assert_brain_repo(repo)
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if check and proc.returncode != 0:
        raise MemoryError_(f"git {' '.join(args[:2])}: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc


def repo_init(city: Path, cfg: dict, hub: str = "") -> Path:
    """Seed a NEW standalone brain repo at write_root (repo mode only)."""
    root = write_root(city, cfg)
    root.mkdir(parents=True, exist_ok=True)
    if not (root / ".git").exists():
        proc = subprocess.run(["git", "init", "-b", "main", str(root)], capture_output=True, text=True)
        if proc.returncode != 0:
            raise MemoryError_(f"git init failed: {proc.stderr.strip()[:300]}")
    git(root, ["config", "core.symlinks", "false"], allow_missing_manifest=True)
    cn = city_name(city, cfg)
    manifest = root / REPO_MANIFEST
    if not manifest.is_file():
        manifest.write_text(
            "# Brain repository — machine-readable manifest.\n"
            "# Presence marks a brain clone; the pack refuses git anywhere without it.\n"
            f"[brain]\nschema = {SCHEMA_VERSION}\nseeded_by = \"{cn}\"\n"
            f"seeded_at = \"{_now_iso()}\"\n")
    git(root, ["config", "user.name", f"{cn}/{agent_name()}"])
    git(root, ["config", "user.email", f"{cn}@{PACK_NAME}.invalid"])
    git(root, ["add", "-A"])
    git(root, ["commit", "--no-verify", "-m", "brain: seed", "--allow-empty"], check=False)
    if hub:
        git(root, ["remote", "add", "origin", hub], check=False)
        git(root, ["push", "-u", "origin", "main"], check=False, timeout=300)
    return root


def repo_sync(city: Path, cfg: dict) -> str:
    """Commit local note changes, pull --no-rebase, push. Local-first: failure
    leaves the commit local; the next heartbeat retries."""
    if sync_mode(cfg) != "repo":
        return "vault mode — sync owned by the vault (no git run)"
    root = write_root(city, cfg)
    _assert_brain_repo(root)
    with tree_lock(city):
        git(root, ["add", "-A"])
        git(root, ["commit", "--no-verify", "-m", f"brain: update from {city_name(city, cfg)}"],
            check=False)
        if git(root, ["remote", "get-url", "origin"], check=False).returncode != 0:
            return "committed locally (no origin remote configured)"
        proc = git(root, ["pull", "--no-rebase", "--no-edit", "origin", "main"], check=False,
                   timeout=300)
        if proc.returncode != 0 and "CONFLICT" in (proc.stderr + proc.stdout):
            git(root, ["merge", "--abort"], check=False)
            raise MemoryError_("merge conflict pulling brain hub (aborted); resolve manually")
        proc = git(root, ["push", "origin", "main"], check=False, timeout=300)
        return "synced (pushed)" if proc.returncode == 0 else \
            f"committed locally; push deferred ({(proc.stderr or proc.stdout).strip()[:120]})"


def _repo_autosync(city: Path, cfg: dict) -> None:
    if sync_mode(cfg) == "repo":
        try:
            repo_sync(city, cfg)
        except MemoryError_:
            pass  # local-first: heartbeat retries
