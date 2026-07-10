#!/usr/bin/env python3
"""Continuity layer tests — REAL-PATH but hermetic (tmpdir cities + fixture
transcripts; the gauge hook and admin commands run as real subprocesses). No
network, no live gc/bd, no vault.

Run: python3 tests/test_continuity.py [-v]
 or: python3 -m unittest discover -s tests -v   (runs this + test_memory.py)
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from importlib.machinery import SourceFileLoader
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK / "scripts"))
import memory_common as mc  # noqa: E402
import continuity_common as cc  # noqa: E402

GAUGE = PACK / "scripts" / "context-gauge.py"
ADMIN = PACK / "scripts" / "memory_admin.py"

# hyphenated script -> load with an explicit loader for last_usage() unit tests
_loader = SourceFileLoader("gauge_mod", str(GAUGE))
_spec = importlib.util.spec_from_loader("gauge_mod", _loader)
gauge = importlib.util.module_from_spec(_spec)
_loader.exec_module(gauge)

NOW = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
TERM = "— end of entry, handed off clean"


def mk_city(continuity=None, enable=True):
    d = Path(tempfile.mkdtemp(prefix="conttest-"))
    (d / "city.toml").write_text("[workspace]\n")
    (d / ".gc").mkdir(parents=True, exist_ok=True)
    cfg = {"city_name": "test-city", "write_root": str(d / "Gas Cities"),
           "sync": {"mode": "vault"}}
    if continuity is not None:
        cfg["continuity"] = continuity
    mc.save_config(d, cfg)
    if enable:
        (d / ".gc" / "cairn.enabled").write_text("enabled test\n")
    return d


def cj(obj):
    """Compact JSON — mirrors real Claude Code JSONL (no spaces after ':'/','),
    which the gauge's substring-based boundary pre-filter expects."""
    return json.dumps(obj, separators=(",", ":"))


def write_jsonl(recs):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    Path(p).write_text("\n".join(cj(r) for r in recs) + "\n")
    return p


def mk_tx(used, model="claude-opus-4-8"):
    return write_jsonl([{"type": "assistant",
                         "message": {"model": model, "usage": {"input_tokens": used}}}])


def run_gauge(city, payload, home=None, alias="artificer-build"):
    env = dict(os.environ)
    env["GC_DIR"] = str(city)
    env["CAIRN_CITY_ROOT"] = str(city)
    env["GC_ALIAS"] = alias
    env["GC_SESSION_NAME"] = "s-test"   # keep the identity fully controlled by `alias`
    env.pop("CAIRN_AGENT", None)
    env.pop("GC_AGENT", None)
    if home:
        env["HOME"] = str(home)
    r = subprocess.run([sys.executable, str(GAUGE)], input=json.dumps(payload),
                       text=True, capture_output=True, env=env, timeout=60)
    return r.stdout.strip()


def run_admin(city, *args, agent="artificer-build"):
    env = dict(os.environ)
    env["CAIRN_CITY_ROOT"] = str(city)
    env["CAIRN_AGENT"] = agent
    return subprocess.run([sys.executable, str(ADMIN), *args],
                          text=True, capture_output=True, env=env, timeout=60)


CONT_ON = {"enabled": True, "enabled_agents": ["artificer"],
           "window": {"overrides": {"claude-opus-4-8": 1000000}}}


class ConfigMergeTests(unittest.TestCase):
    def test_absent_is_off_with_defaults(self):
        c = cc.continuity_cfg({})
        self.assertFalse(c["enabled"])
        self.assertEqual(c["thresholds"], {"normal": 60, "handoff": 75, "urgent": 85})
        self.assertEqual(c["window"]["default"], 200_000)

    def test_partial_merge_keeps_defaults(self):
        c = cc.continuity_cfg({"continuity": {"enabled": True,
                                              "thresholds": {"handoff": 70},
                                              "window": {"overrides": {"m": 5}}}})
        self.assertTrue(c["enabled"])
        self.assertEqual(c["thresholds"]["handoff"], 70)   # overridden
        self.assertEqual(c["thresholds"]["normal"], 60)     # default kept
        self.assertEqual(c["window"]["default"], 200_000)   # default kept
        self.assertEqual(c["window"]["overrides"], {"m": 5})

    def test_agent_resolution(self):
        self.assertEqual(cc.resolve_agent("artificer-build s-1", ["artificer"]), "artificer")
        self.assertIsNone(cc.resolve_agent("mayor s-2", ["artificer", "witness"]))


class NotepadTTLTests(unittest.TestCase):
    def test_section_aware_ttl_and_unstamped(self):
        note = (
            "## In flight\n"
            "- (7/9 1200Z) fresh (age 0)\n"
            "- (7/9 1000Z) 2h -> SUSPECT (fast)\n"
            "- (7/8 0600Z) 30h -> REASSESS (fast)\n"
            "- unstamped bullet\n"
            "## Settled - do NOT re-open\n"
            "- (7/7 1000Z) 50h -> SUSPECT (slow)\n"
            "- (7/1 1200Z) 192h -> REASSESS (slow)\n"
            "> - (1/2 0000Z) blockquote example, ignored\n"
        )
        d = Path(tempfile.mkdtemp())
        np = d / "notepad.md"
        np.write_text(note)
        s = cc.notepad_scan(np, NOW, cc._DEFAULT_CONTINUITY["ttl"])
        self.assertEqual(s["suspect"], 2)
        self.assertEqual(s["reassess"], 2)
        self.assertEqual(s["unstamped"], 1)

    def test_all_fresh_no_flags(self):
        d = Path(tempfile.mkdtemp())
        np = d / "notepad.md"
        np.write_text("## In flight\n- (7/9 1200Z) fresh\n")
        s = cc.notepad_scan(np, NOW, cc._DEFAULT_CONTINUITY["ttl"])
        self.assertEqual((s["suspect"], s["reassess"], s["unstamped"]), (0, 0, 0))


class LastUsageTests(unittest.TestCase):
    def test_skips_synthetic_sidechain_returns_newest_real(self):
        p = write_jsonl([
            {"type": "assistant", "message": {"model": "claude-opus-4-8",
                                              "usage": {"input_tokens": 100}}},
            {"type": "assistant", "isSidechain": True,
             "message": {"model": "m", "usage": {"input_tokens": 9}}},
            {"type": "assistant", "message": {"model": "<synthetic>",
                                              "usage": {"input_tokens": 0}}},
            {"type": "assistant", "message": {"model": "claude-opus-4-8",
                                              "usage": {"input_tokens": 6000,
                                                        "cache_read_input_tokens": 1000}}},
        ])
        used, model, boundary = gauge.last_usage(p)
        self.assertEqual(used, 7000)                 # 6000 + 1000, newest real record
        self.assertEqual(model, "claude-opus-4-8")
        self.assertFalse(boundary)

    def test_compact_boundary_newer(self):
        p = write_jsonl([
            {"type": "assistant", "message": {"model": "m", "usage": {"input_tokens": 5000}}},
            {"type": "summary", "summary": "x"},     # newer than the usage -> boundary
        ])
        _, _, boundary = gauge.last_usage(p)
        self.assertTrue(boundary)


class WindowResolutionTests(unittest.TestCase):
    def setUp(self):
        self.city = mk_city(CONT_ON)

    def test_mapped_override(self):
        tx = mk_tx(770000, model="claude-opus-4-8")
        out = run_gauge(self.city, {"transcript_path": tx, "session_id": "s-map"})
        self.assertIn("~77% of 1000K(mapped)", out)

    def test_assumed_default(self):
        tx = mk_tx(100000, model="claude-sonnet-5")   # no override match
        out = run_gauge(self.city, {"transcript_path": tx, "session_id": "s-def"})
        self.assertIn("~50% of 200K(assumed)", out)

    def test_inferred_when_usage_exceeds_window(self):
        tx = mk_tx(250000, model="claude-sonnet-5")   # 250k > 200k default -> infer 1M
        out = run_gauge(self.city, {"transcript_path": tx, "session_id": "s-inf"})
        self.assertIn("(inferred)", out)
        self.assertIn("1000K", out)

    def test_tee_cache_wins(self):
        home = Path(tempfile.mkdtemp())
        cache = home / ".cache" / "claude-context-windows"
        cache.mkdir(parents=True)
        (cache / "s-tee").write_text("500000")
        tx = mk_tx(250000, model="claude-sonnet-5")
        out = run_gauge(self.city, {"transcript_path": tx, "session_id": "s-tee"}, home=home)
        self.assertIn("~50% of 500K used", out)   # no (assumed)/(mapped)/(inferred) label
        self.assertNotIn("assumed", out)


class GateTests(unittest.TestCase):
    def _tx(self):
        return mk_tx(770000, model="claude-opus-4-8")

    def test_in_scope_emits(self):
        city = mk_city(CONT_ON)
        self.assertTrue(run_gauge(city, {"transcript_path": self._tx(), "session_id": "a"}))

    def test_pack_disabled_silent(self):
        city = mk_city(CONT_ON, enable=False)
        self.assertEqual(run_gauge(city, {"transcript_path": self._tx(), "session_id": "b"}), "")

    def test_continuity_disabled_silent(self):
        city = mk_city({"enabled": False, "enabled_agents": ["artificer"]})
        self.assertEqual(run_gauge(city, {"transcript_path": self._tx(), "session_id": "c"}), "")

    def test_agent_not_listed_silent(self):
        city = mk_city(CONT_ON)
        self.assertEqual(
            run_gauge(city, {"transcript_path": self._tx(), "session_id": "d"}, alias="mayor-x"), "")


class AdminCommandTests(unittest.TestCase):
    def setUp(self):
        self.city = mk_city(CONT_ON)
        self.brain = self.city / "Gas Cities" / "test-city" / "agents" / "artificer" / "brain"

    def test_init_creates_then_idempotent(self):
        r = run_admin(self.city, "continuity-init")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.brain / "notepad.md").is_file())
        self.assertTrue((self.brain / "diary").is_dir())
        tok = cc.read_echo_token(self.brain / "notepad.md")
        r2 = run_admin(self.city, "continuity-init")
        self.assertIn("kept", r2.stdout)
        self.assertEqual(cc.read_echo_token(self.brain / "notepad.md"), tok)  # unchanged

    def test_status_json(self):
        run_admin(self.city, "continuity-init")
        r = run_admin(self.city, "continuity-status")
        info = json.loads(r.stdout)
        self.assertTrue(info["pack_enabled"])
        self.assertTrue(info["continuity_enabled"])
        self.assertIn("artificer", info["agents"])

    def test_handoff_dryrun_side_effect_free(self):
        run_admin(self.city, "continuity-init")
        (self.brain / "diary" / "2026-07-09T120000Z.md").write_text(f"d\n{TERM}\n")
        before = cc.read_echo_token(self.brain / "notepad.md")
        r = run_admin(self.city, "continuity-handoff", "--bead", "hs-x", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("DRY-RUN", r.stdout)
        self.assertEqual(cc.read_echo_token(self.brain / "notepad.md"), before)  # not rotated

    def test_handoff_refuses_without_terminator(self):
        run_admin(self.city, "continuity-init")
        (self.brain / "diary" / "2026-07-10T000000Z.md").write_text("no terminator\n")
        r = run_admin(self.city, "continuity-handoff", "--bead", "hs-x", "--dry-run")
        self.assertEqual(r.returncode, 3)
        self.assertIn("NO terminator", r.stderr)

    def test_disabled_gate_refuses_init(self):
        (self.city / ".gc" / "cairn.enabled").unlink()
        r = run_admin(self.city, "continuity-init")
        self.assertEqual(r.returncode, 1)
        self.assertIn("disabled", r.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
