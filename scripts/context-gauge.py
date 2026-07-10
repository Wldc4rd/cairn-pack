#!/usr/bin/env python3
# context-gauge.py — Claude Code UserPromptSubmit hook: inject an agent's REAL
# context usage into each turn. Part of the Cairn continuity layer.
#
# WHY: agents cannot sense their own context; they pattern-match on transcript
# "feel" and get anxious early or blow past a safe handoff. The statusline
# renders the true % for the human only; this hook tees the number INTO the turn
# so the agent acts on data, not vibes.
#
# DEFAULT OFF, triple-gated (see continuity_common): pack enabled marker +
# continuity.enabled + agent listed in continuity.enabled_agents. Any gate
# missing => zero output.
#
# FAIL-SILENT CONTRACT: any error => print nothing, exit 0. Absence of a gauge
# line means UNKNOWN, never zero — the protocol text says so. This posture
# matches the pack's background arms (never raise into the host session).
#
# WINDOW: hooks don't receive context_window_size (statuslines do). Resolution
# chain: statusline-tee cache -> config override by model substring -> inference
# (usage proves the window is bigger) -> config default, labeled "(assumed)".
#
# Stdlib only (+ the sibling continuity_common / memory_common).

import json
import os
import sys
import time
from datetime import datetime, timezone

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import continuity_common as cc
    import memory_common as mc
except Exception:  # pragma: no cover - layer absent => silent
    cc = mc = None


def last_usage(path):
    """Newest REAL assistant usage record, scanning backwards in chunks. Returns
    (used, model, compact_boundary_is_newer). Skips synthetic/zero + sidechain
    records. Self-contained (no deps) on purpose."""
    size = os.path.getsize(path)
    chunk = 512_000
    overlap = 8_000
    pos = size
    seen_boundary = False
    with open(path, "rb") as f:
        while pos > 0:
            start = max(0, pos - chunk)
            f.seek(start)
            tail = f.read(pos - start + (overlap if pos < size else 0))
            lines = tail.decode("utf-8", "replace").splitlines()
            if start > 0 and lines:
                lines = lines[1:]  # first line may be partial
            for line in reversed(lines):
                if '"type":"summary"' in line or "compact_boundary" in line or '"isCompactSummary":true' in line:
                    seen_boundary = True
                    continue
                if '"usage"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                msg = rec.get("message") or {}
                usage = msg.get("usage") or {}
                if rec.get("type") != "assistant" or usage.get("input_tokens") is None:
                    continue
                if rec.get("isSidechain") or rec.get("isApiErrorMessage"):
                    continue  # subagent/API-error records aren't THIS session's context
                model = str(msg.get("model") or "")
                used = (int(usage.get("input_tokens") or 0)
                        + int(usage.get("cache_read_input_tokens") or 0)
                        + int(usage.get("cache_creation_input_tokens") or 0))
                if used == 0 or "<synthetic>" in model:
                    continue  # rate-limit/synthetic event, keep scanning
                return used, model, seen_boundary
            pos = start
            if size - pos > 5_000_000:
                break
    return None, "", seen_boundary


def notepad_line(brain, ttl):
    """Section-aware notepad staleness report, or '' — delegated to cc."""
    npath = cc.notepad_path(brain)
    if not npath.is_file():
        return ""
    scan = cc.notepad_scan(npath, datetime.now(timezone.utc), ttl)
    if not scan:
        return ""
    now = datetime.now(timezone.utc)
    parts = []
    if scan.get("newest") is not None:
        idle_min = int((now - scan["newest"]).total_seconds() / 60)
        if idle_min >= 45:
            parts.append(f"newest notepad stamp {idle_min}m old — if a milestone/"
                         f"correction happened since, update it now")
    if scan.get("reassess"):
        parts.append(f"{scan['reassess']} line(s) past RE-ASSESS TTL — re-affirm w/ fresh stamp or prune")
    if scan.get("suspect"):
        parts.append(f"{scan['suspect']} line(s) SUSPECT (past section TTL) — re-verify before trusting")
    if scan.get("unstamped"):
        parts.append(f"{scan['unstamped']} bullet(s) unstamped/malformed — stamp them (M/D HHMMZ) or they never expire")
    return (" | notepad: " + "; ".join(parts) + ".") if parts else ""


def main():
    if cc is None:
        return
    try:
        city = mc.find_city_root(os.environ.get("GC_DIR") or None)
        cfg = mc.load_config(city)
        ccfg = cc.continuity_cfg(cfg)
        agent = cc.in_scope(city, ccfg, cc.env_identity())
    except Exception:
        return
    if not agent:
        return  # a gate is closed => silent (default OFF)

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    path = payload.get("transcript_path") or ""
    if not path or not os.path.isfile(path):
        return

    # breadcrumb (crash forensics) — fail-silent
    try:
        rt = cc.runtime_dir(city, ccfg)
        rt.mkdir(parents=True, exist_ok=True)
        cc.breadcrumb_path(city, ccfg, agent).write_text(
            f"{path}\n{datetime.now(timezone.utc).isoformat()}\n")
    except Exception:
        pass

    try:
        used, model, boundary_newer = last_usage(path)
    except Exception:
        return
    if used is None:
        return
    if boundary_newer:
        print("context-gauge: post-compact — usage unknown this turn (a compaction "
              "just happened; the next reading will be real). Re-read your notepad; "
              "work normally.")
        return

    thr = ccfg["thresholds"]
    T_NORMAL, T_HANDOFF, T_URGENT = thr["normal"], thr["handoff"], thr["urgent"]

    # window resolution (dynamic-first)
    window, assumed = ccfg["window"]["default"], "(assumed)"
    try:
        sid = str(payload.get("session_id") or "")
        cache = os.path.expanduser(f"~/.cache/claude-context-windows/{sid}")
        if sid and os.path.isfile(cache):
            with open(cache) as fh:
                w = int(fh.read().strip())
            if w > 0:
                window, assumed = w, ""
    except Exception:
        pass
    if assumed:
        for key, win in (ccfg["window"].get("overrides") or {}).items():
            if key in model:
                window, assumed = int(win), "(mapped)"
                break
    if used > window:  # usage proves the window is bigger than we think
        window, assumed = 1_000_000, "(inferred)"
    pct = min(999, round(100 * used / window))

    # consecutive-URGENT escalation
    state = cc.state_path(city, ccfg, agent)
    urgent_n = 0
    try:
        if state.is_file():
            st = json.loads(state.read_text())
        else:
            st = {}
        urgent_n = int(st.get("consecutive_urgent") or 0)
    except Exception:
        st = {}
    urgent_n = urgent_n + 1 if pct >= T_URGENT else 0
    try:
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({"consecutive_urgent": urgent_n, "at": time.time()}))
    except Exception:
        pass

    if pct >= T_URGENT:
        if urgent_n >= 3:
            policy = (f"URGENT x{urgent_n} consecutive turns — the handoff has NOT "
                      f"executed. Run `gc cairn continuity-handoff` again and PASTE ITS "
                      f"FULL OUTPUT/ERROR into your bead; if it exits nonzero, mail your "
                      f"coordinator the error and stop starting new work.")
        else:
            policy = (f"URGENT: update notepad (one line is enough), write/append your "
                      f"diary entry with its terminator, run `gc cairn continuity-handoff` "
                      f"NOW (>= {T_URGENT}%). Past 90%, stop mid-unit — a stub diary beats "
                      f"a truncated session.")
    elif pct >= T_HANDOFF:
        policy = (f"handoff window (>= {T_HANDOFF}%): finish the CURRENT unit or ~10 more "
                  f"minutes, WHICHEVER IS SMALLER; then notepad, diary entry (+terminator), "
                  f"`gc cairn continuity-handoff`. Do not start new units.")
    elif pct >= T_NORMAL:
        policy = (f"work normally; take a natural handoff boundary if one appears "
                  f"({T_NORMAL}-{T_HANDOFF}%). Deferring work 'for context' is still prohibited.")
    else:
        policy = ""  # below normal: bare number only — the number informs, prose anxietizes

    line = f"context-gauge: ~{pct}% of {window // 1000}K{assumed} used ({used:,} tokens)."
    if policy:
        line += f" Policy: {policy}"

    try:
        brain = cc.brain_dir(city, cfg, agent)
        line += notepad_line(brain, ccfg["ttl"])
    except Exception:
        pass

    print(line)


if __name__ == "__main__":
    main()
