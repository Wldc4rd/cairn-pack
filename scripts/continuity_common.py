#!/usr/bin/env python3
"""Continuity common — shared logic for the Cairn continuity layer.

The continuity layer = a context-usage gauge (a Claude Code UserPromptSubmit
hook) + a disciplined notepad/diary/handoff protocol. It gives an agent a REAL
context-% reading each turn and a durable WAL (notepad + diary + issue tracker)
so a fresh session recovers state instead of guessing on transcript "feel".

DEFAULT OFF, triple-gated (same posture as the rest of the pack):
  1. the pack master switch — `.gc/cairn.enabled` (mc.is_enabled)
  2. `continuity.enabled = true` in the per-city config
  3. the session's agent is listed in `continuity.enabled_agents`
Miss any gate and the layer is silent / a no-op. An agent with no gauge has no
business self-handing-off on feel — so unlisted agents are explicitly exempt
(anti-cosplay).

Config lives in the pack's own JSON config (`.gc/services/cairn/config.json`,
the `continuity` section) — no new file format, no new dependency. The
path-derivation reuses memory_common's roots so the notepad/diary land in the
agent's brain dir (`<write_root>/<city>/agents/<agent>/brain/`), exactly where
the memory notes live.

Stdlib only.
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_common as mc  # noqa: E402

TERMINATOR = "— end of entry, handed off clean"
DIARY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z\.md$")
STAMP_RE = re.compile(r"^\s*-\s*\((\d{1,2})/(\d{1,2})\s+(\d{3,4})Z\)")
BULLET_RE = re.compile(r"^\s*-\s+\S")
SECTION_RE = re.compile(r"^##\s+(.*)")
ECHO_RE = re.compile(r"^echo-token:\s*(\S+)", re.MULTILINE)
FUTURE_TOLERANCE_H = 0.25  # 15 min clock-skew grace before the year-1 fallback

# Defaults merged UNDER the per-city `continuity` config (absence => OFF/safe),
# mirroring memory_common's budgets() pattern.
_DEFAULT_CONTINUITY = {
    "enabled": False,
    "enabled_agents": [],
    "thresholds": {"normal": 60, "handoff": 75, "urgent": 85},
    "window": {"default": 200_000, "overrides": {}},
    "ttl": {"suspect_h": 1, "reassess_h": 24,
            "settled_suspect_h": 48, "settled_reassess_h": 168},
    "paths": {},  # optional brain_root / runtime template overrides
}


def continuity_cfg(cfg: dict) -> dict:
    """Defaults merged under cfg['continuity']; nested sections merged one level."""
    import copy
    c = copy.deepcopy(_DEFAULT_CONTINUITY)
    user = cfg.get("continuity") or {}
    for k, v in user.items():
        if k in ("thresholds", "window", "ttl", "paths") and isinstance(v, dict):
            c[k].update(v)
        else:
            c[k] = v
    return c


def enabled_agents(ccfg: dict) -> list[str]:
    a = ccfg.get("enabled_agents") or []
    return [str(x) for x in a] if isinstance(a, list) else []


def resolve_agent(identity: str, agents: list[str]) -> str | None:
    """First enabled_agents substring present in the identity => canonical name."""
    for name in agents:
        if name and name in identity:
            return name
    return None


def env_identity() -> str:
    return " ".join(os.environ.get(v, "") for v in
                    ("CAIRN_AGENT", "GC_ALIAS", "GC_AGENT", "GC_SESSION_NAME"))


def in_scope(city: Path, ccfg: dict, identity: str) -> str | None:
    """Return the canonical agent name IF all three gates pass, else None."""
    if not mc.is_enabled(city):
        return None
    if not ccfg.get("enabled"):
        return None
    return resolve_agent(identity, enabled_agents(ccfg))


def _expand(tmpl: str, city_dir: str, agent: str) -> str:
    return tmpl.format(home=os.path.expanduser("~"), city_dir=city_dir, agent=agent)


def brain_dir(city: Path, cfg: dict, agent: str) -> Path:
    """The agent's brain dir. Default via memory_common roots (matches where
    memory notes live); overridable with continuity.paths.brain_root
    (templated {home}/{city_dir}/{agent} => <brain_root>/<agent>/brain)."""
    ccfg = continuity_cfg(cfg)
    br = (ccfg.get("paths") or {}).get("brain_root")
    if br:
        return Path(_expand(br, str(city), agent)) / agent / "brain"
    root = mc.write_root(city, cfg)
    return mc.scope_dir(root, "agent", mc.city_name(city, cfg), agent)


def runtime_dir(city: Path, ccfg: dict) -> Path:
    rt = (ccfg.get("paths") or {}).get("runtime")
    if rt:
        return Path(_expand(rt, str(city), ""))
    return city / ".gc" / "runtime"


def notepad_path(brain: Path) -> Path:
    return brain / "notepad.md"


def diary_dir(brain: Path) -> Path:
    return brain / "diary"


def state_path(city: Path, ccfg: dict, agent: str) -> Path:
    return runtime_dir(city, ccfg) / f"context-gauge-state-{agent}.json"


def breadcrumb_path(city: Path, ccfg: dict, agent: str) -> Path:
    return runtime_dir(city, ccfg) / f"{agent}-last-transcript"


# ---- diary + notepad helpers (shared by gauge + commands) ------------------

def diary_entries(dpath: Path) -> list[str]:
    """Entry filenames in PLAIN sort (never mtime — vault sync resets it)."""
    if not dpath.is_dir():
        return []
    return sorted(f.name for f in dpath.iterdir() if DIARY_RE.match(f.name))


def latest_diary(dpath: Path) -> str | None:
    e = diary_entries(dpath)
    return e[-1] if e else None


def has_terminator(diary_file: Path) -> bool:
    try:
        return TERMINATOR in diary_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def read_echo_token(npath: Path) -> str | None:
    try:
        m = ECHO_RE.search(npath.read_text(encoding="utf-8", errors="replace"))
        return m.group(1) if m else None
    except OSError:
        return None


def notepad_scan(npath: Path, now: datetime, ttl: dict) -> dict:
    """newest stamp + section-aware TTL tier counts + unstamped count.
    Returns {} if unreadable. Mirrors the gauge's staleness logic exactly."""
    try:
        txt = npath.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    section = ""
    newest = None
    suspect = reassess = unstamped = 0
    for raw in txt.splitlines():
        msec = SECTION_RE.match(raw)
        if msec:
            section = msec.group(1).lower()
            continue
        if raw.lstrip().startswith(">"):
            continue
        if not section or not BULLET_RE.match(raw):
            continue
        m = STAMP_RE.match(raw)
        if not m:
            unstamped += 1
            continue
        mo, d, hhmm = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            stamp = datetime(now.year, mo, d, int(hhmm[:-2]), int(hhmm[-2:]),
                             tzinfo=timezone.utc)
        except ValueError:
            unstamped += 1
            continue
        if (stamp - now).total_seconds() / 3600 > FUTURE_TOLERANCE_H:
            stamp = stamp.replace(year=now.year - 1)
        age_h = (now - stamp).total_seconds() / 3600
        if newest is None or stamp > newest:
            newest = stamp
        slow = ("settled" in section) or ("gotcha" in section)
        if slow:
            s_thr, r_thr = ttl["settled_suspect_h"], ttl["settled_reassess_h"]
        else:
            s_thr, r_thr = ttl["suspect_h"], ttl["reassess_h"]
        if age_h >= r_thr:
            reassess += 1
        elif age_h >= s_thr:
            suspect += 1
    return {"newest": newest, "suspect": suspect,
            "reassess": reassess, "unstamped": unstamped}


def new_token() -> str:
    """Random 4-char echo-token (os.urandom => no seed/state deps)."""
    return os.urandom(2).hex()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp_bullet(now: datetime) -> str:
    return f"{now.month}/{now.day} {now:%H%M}Z"


def target_agents(ccfg: dict, explicit: str | None) -> list[str]:
    if explicit:
        return [explicit]
    agents = enabled_agents(ccfg)
    if not agents:
        raise mc.MemoryError_("continuity config has no enabled_agents and no --agent given")
    return agents
