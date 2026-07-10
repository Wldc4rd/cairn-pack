#!/usr/bin/env python3
# statusline-tee.py — a statusLine WRAPPER for the Cairn continuity layer.
# Solves the "the context gauge needs the window size, but only the statusline
# receives it" problem without making the operator's statusline a dependency.
#
# WHY: Claude Code hands context_window_size ONLY to the statusline (verified:
# not in hook stdin, not in transcripts). The context-gauge hook needs it. So a
# deploying operator points their `statusLine.command` at THIS wrapper, which:
#   1) TEEs the true per-session window to ~/.cache/claude-context-windows/<sid>
#      (what the gauge reads; its resolution chain degrades honestly without it).
#   2) DELEGATEs rendering to the operator's OWN statusline (auto-discovered from
#      ~/.claude/settings.json, or set DELEGATE below) with the identical payload
#      — their UI is preserved exactly, their script needs NO changes and is NOT
#      a dependency.
#   3) If no operator statusline exists, prints a minimal line.
#
# Fail-open on every step: a broken tee never breaks rendering; a broken delegate
# falls back to the minimal line. Stdlib only. Installed out-of-pack (statusLine
# lives in the provider settings, not the pack) — see the README "Continuity".

import json
import os
import subprocess
import sys

DELEGATE = ""  # "" = auto-discover from ~/.claude/settings.json; "none" = always minimal render

raw = sys.stdin.read()
try:
    payload = json.loads(raw or "{}")
except Exception:
    payload = {}

# 1) tee the window (fail-silent)
try:
    sid = str(payload.get("session_id") or "")
    win = (payload.get("context_window") or {}).get("context_window_size")
    if sid and win:
        cdir = os.path.expanduser("~/.cache/claude-context-windows")
        os.makedirs(cdir, exist_ok=True)
        path = os.path.join(cdir, sid)
        cur = open(path).read().strip() if os.path.isfile(path) else None
        if cur != str(win):
            with open(path, "w") as fh:
                fh.write(str(win))
except Exception:
    pass

# 2) delegate to the operator's own statusline, if any
cmd = DELEGATE
if not cmd:
    try:
        with open(os.path.expanduser("~/.claude/settings.json")) as fh:
            cfg = json.load(fh) or {}
        cmd = (cfg.get("statusLine") or {}).get("command") or ""
    except Exception:
        cmd = ""
if cmd and cmd.lower() != "none" and "statusline-tee" not in cmd:  # never self-recurse
    try:
        r = subprocess.run(cmd, shell=True, input=raw, capture_output=True,
                           text=True, timeout=5)
        if r.returncode == 0 and r.stdout:
            sys.stdout.write(r.stdout)
            sys.exit(0)
    except Exception:
        pass

# 3) minimal fallback render
model = ((payload.get("model") or {}).get("display_name")) or "Claude"
win = (payload.get("context_window") or {}).get("context_window_size")
print(f"{model} | window: {win or '?'} | continuity tee active")
