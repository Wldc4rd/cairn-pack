#!/usr/bin/env python3
"""Memory pack tests — REAL-PATH by design (markdown-in-git/vault backend).

Throwaway cities + brain trees under tmpdirs; the MCP server is exercised as a
subprocess speaking real stdio JSON-RPC; repo mode round-trips through a real
bare hub; bd interactions use a fake `bd` shim on PATH. Includes the write-
confinement tests mirrored from bartertown/postman (traversal + symlink refusal).

Run: python3 tests/test_memory.py [-v]
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACK / "scripts"))
import memory_common as mc  # noqa: E402

ADMIN = PACK / "scripts" / "memory_admin.py"
MCP = PACK / "scripts" / "memory_mcp.py"


class FakeCity:
    def __init__(self, root: Path, name: str, write_root: Path | None = None, **cfg_extra):
        self.root = root
        self.name = name
        root.mkdir(parents=True, exist_ok=True)
        (root / "city.toml").write_text("[workspace]\n")
        (root / ".gc").mkdir(exist_ok=True)
        self.write_root = write_root or (root / "Gas Cities")
        cfg = {"city_name": name, "write_root": str(self.write_root),
               "sync": {"mode": "vault"}, **cfg_extra}
        mc.save_config(root, cfg)

    @property
    def cfg(self) -> dict:
        return mc.load_config(self.root)

    def env(self, agent: str = "test-agent") -> dict:
        env = dict(os.environ)
        env["CAIRN_CITY_ROOT"] = str(self.root)
        env["CAIRN_AGENT"] = agent
        return env

    def admin(self, *args: str, agent: str = "test-agent") -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(ADMIN), *args],
                              capture_output=True, text=True, env=self.env(agent), timeout=120)

    def enable(self):
        r = self.admin("enable", "--reviewed-by", "test-suite")
        assert r.returncode == 0, r.stderr

    def remember(self, key, body, **kw):
        kw.setdefault("agent", "test-agent")
        return mc.remember(self.root, self.cfg, key, body, **kw)


def fake_bd(dir_: Path, status: str) -> Path:
    """A `bd` shim whose `show <id> --json` reports the given status."""
    dir_.mkdir(parents=True, exist_ok=True)
    sh = dir_ / "bd"
    sh.write_text("#!/bin/sh\n"
                  f"echo '{{\"id\": \"x\", \"status\": \"{status}\"}}'\n")
    sh.chmod(0o755)
    return dir_


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="memtest-"))
        self.city = FakeCity(self.tmp / "cityA", "citya")
        self.city.enable()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestStoreBasics(Base):
    def test_remember_creates_agent_note(self):
        p = self.city.remember("first-key", "hello world", tags=["alpha"])
        self.assertTrue(p.is_file())
        self.assertIn("agents/test-agent/brain", str(p))
        meta, body = mc.fm_parse(p.read_text())
        self.assertEqual(meta["key"], "first-key")
        self.assertEqual(meta["type"], "memory")
        self.assertIn("hello world", body)
        self.assertTrue((p.parent / "_index.md").is_file())

    def test_update_in_place_preserves_created(self):
        p1 = self.city.remember("dup-key", "v1")
        meta1, _ = mc.fm_parse(p1.read_text())
        p2 = self.city.remember("dup-key", "v2")
        self.assertEqual(p1, p2)
        meta2, body2 = mc.fm_parse(p2.read_text())
        self.assertEqual(meta1["created"], meta2["created"])
        self.assertIn("v2", body2)
        notes = mc.scan_scope(self.city.write_root, "agent", "citya", "test-agent")
        self.assertEqual(len([n for n in notes if n["key"] == "dup-key"]), 1)

    def test_scopes_and_nation_propose_refusal(self):
        self.city.remember("city-fact", "shared", scope="city")
        self.assertTrue(any(n["key"] == "city-fact"
                            for n in mc.scan_scope(self.city.write_root, "city", "citya", "x")))
        with self.assertRaisesRegex(mc.MemoryError_, "propose"):
            self.city.remember("nation-fact", "fleet", scope="nation")

    def test_nation_direct_when_configured(self):
        cfg = self.city.cfg
        cfg["nation_write"] = "direct"
        mc.save_config(self.city.root, cfg)
        self.city.remember("nation-fact", "fleet", scope="nation")
        self.assertTrue(any(n["key"] == "nation-fact"
                            for n in mc.scan_scope(self.city.write_root, "nation", "citya", "x")))

    def test_forget(self):
        self.city.remember("gone-soon", "x")
        self.assertTrue(mc.forget(self.city.root, self.city.cfg, "gone-soon", agent="test-agent"))
        self.assertFalse(mc.forget(self.city.root, self.city.cfg, "gone-soon", agent="test-agent"))

    def test_disabled_gate(self):
        (self.city.root / mc.ENABLE_MARKER).unlink()
        with self.assertRaisesRegex(mc.MemoryError_, "disabled"):
            self.city.remember("nope", "x")
        with self.assertRaisesRegex(mc.MemoryError_, "disabled"):
            mc.recall_digest(self.city.root, self.city.cfg)

    def test_invalid_keys_refused(self):
        for bad in ("../escape", "UPPER", "", "a/b", "-lead"):
            with self.assertRaises(mc.MemoryError_):
                self.city.remember(bad, "x")

    def test_oversize_body_refused(self):
        with self.assertRaisesRegex(mc.MemoryError_, "max_note_bytes"):
            self.city.remember("big", "x" * 20000)


class TestConfinement(Base):
    def test_hostile_agent_name_stays_inside(self):
        p = self.city.remember("esc", "x", agent="../../../etc")
        self.assertTrue(str(p.resolve()).startswith(str(self.city.write_root.resolve())))

    def test_symlinked_brain_dir_write_refused(self):
        cn = "citya"
        outside = self.tmp / "outside"
        outside.mkdir()
        agents = self.city.write_root / cn / "agents" / "evil"
        agents.parent.mkdir(parents=True, exist_ok=True)
        agents.mkdir(exist_ok=True)
        (agents / "brain").symlink_to(outside)
        with self.assertRaisesRegex(mc.MemoryError_, "symlink|outside"):
            self.city.remember("pwn", "x", agent="evil")

    def test_symlinked_note_not_read(self):
        secret = self.tmp / "secret.txt"
        secret.write_text("---\nkey: leaked-secret\n---\nTOPSECRET")
        d = mc.scope_dir(self.city.write_root, "city", "citya", "x")
        d.mkdir(parents=True, exist_ok=True)
        (d / "2026-01-01-leaked-secret.md").symlink_to(secret)
        notes = mc.scan_scope(self.city.write_root, "city", "citya", "x")
        self.assertFalse(any("TOPSECRET" in n["body"] for n in notes))

    def test_confine_write_rejects_outside(self):
        with self.assertRaises(mc.MemoryError_):
            mc.confine_write(self.city.write_root, self.tmp / "elsewhere" / "f.md")


class TestLint(Base):
    def test_secret_refused(self):
        with self.assertRaisesRegex(mc.MemoryError_, "secret lint"):
            self.city.remember("token-note", "here ghp_" + "a" * 24)

    def test_banned_string_refused(self):
        cfg = self.city.cfg
        cfg["lint"] = {"banned_strings": ["fam-name"]}
        mc.save_config(self.city.root, cfg)
        with self.assertRaisesRegex(mc.MemoryError_, "banned"):
            self.city.remember("oops", "mentions Fam-Name here")


class TestRecall(Base):
    def _with_bd(self, status):
        shim = fake_bd(self.tmp / "bin", status)
        old = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{shim}:{old}"
        self.addCleanup(os.environ.__setitem__, "PATH", old)

    def test_scope_dedup_most_specific_wins(self):
        self.city.remember("same-key", "AGENT version")
        self.city.remember("same-key", "CITY version", scope="city")
        notes = mc.gather(self.city.root, self.city.cfg, agent="test-agent")
        hits = [n for n in notes if n["key"] == "same-key"]
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["scope"], "agent")
        self.assertIn("AGENT", hits[0]["body"])

    def test_rank_pinned_then_open_handoff_then_recent(self):
        self._with_bd("open")
        self.city.remember("old-plain", "old", scope="city")
        self.city.remember("live-handoff", "resume here", type_="handoff", bead="ab-1")
        self.city.remember("pinned-rule", "always", pinned=True, scope="city")
        self.city.remember("new-plain", "new")
        ranked = mc.rank(self.city.root, mc.gather(self.city.root, self.city.cfg, agent="test-agent"))
        keys = [n["key"] for n in ranked]
        self.assertEqual(keys[0], "pinned-rule")
        self.assertEqual(keys[1], "live-handoff")
        self.assertLess(keys.index("new-plain"), keys.index("old-plain"))

    def test_handoff_demotes_when_bead_closed(self):
        self._with_bd("closed")
        self.city.remember("done-handoff", "was in flight", type_="handoff", bead="ab-2")
        self.city.remember("pinned-rule", "always", pinned=True)
        ranked = mc.rank(self.city.root, mc.gather(self.city.root, self.city.cfg, agent="test-agent"))
        done = next(n for n in ranked if n["key"] == "done-handoff")
        self.assertFalse(done["open_handoff"])
        digest = mc.render_digest(self.city.root, self.city.cfg, ranked)
        self.assertIn("handoff/closed", digest)

    def test_budget_caps_and_tail(self):
        for i in range(12):
            self.city.remember(f"note-{i}", ("word " * 120))
        digest = mc.recall_digest(self.city.root, self.city.cfg, agent="test-agent",
                                  budget_tokens=400)
        self.assertLess(len(digest) // 4, 700)  # budget + tail slack
        self.assertRegex(digest, r"\d+ more memor(y|ies) not shown")

    def test_entry_clip(self):
        cfg = self.city.cfg
        cfg["budgets"] = {"entry_char_cap": 100}
        mc.save_config(self.city.root, cfg)
        self.city.remember("long-note", "z" * 500)
        digest = mc.recall_digest(self.city.root, self.city.cfg, agent="test-agent")
        self.assertIn("clipped", digest)

    def test_read_roots_merge(self):
        mirror = self.tmp / "mirror-root"
        d = mirror / "brain"
        d.mkdir(parents=True)
        (d / "2026-01-01-mirrored-fact.md").write_text(
            mc.fm_render({"key": "mirrored-fact", "type": "memory",
                          "created": "2026-01-01T00:00:00Z", "updated": "2026-01-01T00:00:00Z",
                          "pinned": False}, "came from the slice"))
        cfg = self.city.cfg
        cfg["read_roots"] = [str(mirror)]
        mc.save_config(self.city.root, cfg)
        notes = mc.gather(self.city.root, self.city.cfg, agent="test-agent")
        self.assertTrue(any(n["key"] == "mirrored-fact" for n in notes))


class TestPrimeRegion(Base):
    def _prime(self, with_markers=True):
        p = self.city.root / ".beads" / "PRIME.md"
        p.parent.mkdir(exist_ok=True)
        region = f"{mc.PRIME_BEGIN}\nstale\n{mc.PRIME_END}\n" if with_markers else ""
        p.write_text("# Head\nkeep-top\n" + region + "keep-bottom\n")
        return p

    def test_rewrites_only_region_idempotently(self):
        p = self._prime()
        self.city.remember("fresh", "fact", scope="city")
        for _ in range(2):
            digest = mc.recall_digest(self.city.root, self.city.cfg, scopes=("city", "nation"))
            mc.write_prime_region(self.city.root, digest)
        text = p.read_text()
        self.assertIn("keep-top", text)
        self.assertIn("keep-bottom", text)
        self.assertNotIn("stale", text)
        self.assertIn("fresh", text)
        self.assertEqual(text.count(mc.PRIME_BEGIN), 1)

    def test_refuses_without_markers(self):
        self._prime(with_markers=False)
        with self.assertRaisesRegex(mc.MemoryError_, "no .* region|markers"):
            mc.write_prime_region(self.city.root, "digest")

    def test_write_prime_cli_two_key(self):
        p = self._prime()
        before = p.read_text()
        r = self.city.admin("recall", "--write-prime")  # enabled but NOT armed
        self.assertEqual(r.returncode, 0)
        self.assertIn("not armed", r.stderr + r.stdout)
        self.assertEqual(p.read_text(), before)
        (self.city.root / mc.RECALL_ARM_MARKER).write_text("armed\n")
        r = self.city.admin("recall", "--write-prime")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotEqual(p.read_text(), before)


class TestSearch(Base):
    def setUp(self):
        super().setUp()
        self.city.remember("mine", "the zanzibar protocol", agent="test-agent")
        self.city.remember("shared", "zanzibar shared", scope="city")
        self.city.remember("theirs", "zanzibar sibling secret sauce", agent="sibling-agent")

    def test_chain_excludes_siblings(self):
        hits = mc.search(self.city.root, self.city.cfg, "zanzibar", agent="test-agent")
        keys = {h["key"] for h in hits}
        self.assertIn("mine", keys)
        self.assertIn("shared", keys)
        self.assertNotIn("theirs", keys)

    def test_city_scope_spans_siblings(self):
        hits = mc.search(self.city.root, self.city.cfg, "zanzibar", agent="test-agent",
                         scope="city")
        keys = {h["key"] for h in hits}
        self.assertIn("theirs", keys)
        their = next(h for h in hits if h["key"] == "theirs")
        self.assertIn("sibling", their["scope"])
        self.assertIn("zanzibar", their["snippet"].lower() + their["key"])


    def test_multi_term_and_semantics(self):
        hits = mc.search(self.city.root, self.city.cfg, "zanzibar protocol", agent="test-agent")
        self.assertEqual({h["key"] for h in hits}, {"mine"})
        hits = mc.search(self.city.root, self.city.cfg, "zanzibar nothere", agent="test-agent")
        self.assertEqual(hits, [])

    def test_search_requires_query(self):
        with self.assertRaises(mc.MemoryError_):
            mc.search(self.city.root, self.city.cfg, "  ")


class TestAdminAndMigration(Base):
    def test_init_and_status(self):
        fresh = FakeCity(self.tmp / "cityB", "cityb")
        r = fresh.admin("init", "--write-root", str(self.tmp / "cityB" / "Gas Cities"),
                        "--mode", "vault")
        self.assertEqual(r.returncode, 0, r.stderr)
        r = fresh.admin("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        info = json.loads(r.stdout)
        self.assertEqual(info["city"], "cityb")
        self.assertFalse(info["enabled"])

    EXPORT = (
        "# Beads Workflow Context\n\n## Persistent Memories (3)\n\nStored via bd.\n\n"
        "### alpha-key\nAlpha body line.\nSecond line.\n\n"
        "### Beta Key!\nBeta body.\n\n"
        "### gamma-key\nGamma body.\n\n"
        "## Something Else\nnot a memory\n"
    )

    def test_migrate_bd(self):
        exp = self.tmp / "export.txt"
        exp.write_text(self.EXPORT)
        r = self.city.admin("migrate-bd", "--from-file", str(exp), "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["migrated"], 3)
        r = self.city.admin("migrate-bd", "--from-file", str(exp))
        self.assertEqual(r.returncode, 0, r.stderr)
        notes = mc.scan_scope(self.city.write_root, "city", "citya", "x")
        keys = {n["key"] for n in notes}
        self.assertTrue({"alpha-key", "beta-key", "gamma-key"} <= keys, keys)
        alpha = next(n for n in notes if n["key"] == "alpha-key")
        self.assertIn("Second line", alpha["body"])
        self.assertNotIn("something-else", keys)
        # idempotent re-run: update-in-place, no duplicates
        r = self.city.admin("migrate-bd", "--from-file", str(exp))
        self.assertEqual(r.returncode, 0, r.stderr)
        notes2 = mc.scan_scope(self.city.write_root, "city", "citya", "x")
        self.assertEqual(len([n for n in notes2 if n["key"] == "alpha-key"]), 1)


class TestMCP(Base):
    def _rpc(self, requests: list[dict], agent="test-agent") -> list[dict]:
        inp = "".join(json.dumps(r) + "\n" for r in requests)
        proc = subprocess.run([sys.executable, str(MCP)], input=inp, capture_output=True,
                              text=True, env=self.city.env(agent), timeout=60)
        return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]

    def test_round_trip(self):
        out = self._rpc([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "memory_remember",
                        "arguments": {"key": "mcp-key", "body": "via mcp", "scope": "city"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "memory_recall", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
             "params": {"name": "memory_search", "arguments": {"query": "via mcp"}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call",
             "params": {"name": "memory_forget", "arguments": {"key": "mcp-key", "scope": "city"}}},
        ])
        by_id = {r["id"]: r for r in out}
        tools = {t["name"] for t in by_id[2]["result"]["tools"]}
        self.assertEqual(tools, {"memory_remember", "memory_recall", "memory_search", "memory_forget"})
        self.assertIn("Remembered", by_id[3]["result"]["content"][0]["text"])
        self.assertIn("mcp-key", by_id[4]["result"]["content"][0]["text"])
        self.assertIn("mcp-key", by_id[5]["result"]["content"][0]["text"])
        self.assertIn("Forgot", by_id[6]["result"]["content"][0]["text"])

    def test_disabled_is_error(self):
        (self.city.root / mc.ENABLE_MARKER).unlink()
        out = self._rpc([{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "memory_recall", "arguments": {}}}])
        self.assertTrue(out[0]["result"].get("isError"))
        self.assertIn("disabled", out[0]["result"]["content"][0]["text"])


class TestRepoMode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="memrepo-"))
        self.hub = self.tmp / "hub.git"
        subprocess.run(["git", "init", "--bare", "-b", "main", str(self.hub)],
                       capture_output=True, check=True)
        self.city = FakeCity(self.tmp / "cityR", "cityr",
                             write_root=self.tmp / "cityR" / "brain-repo",
                             sync={"mode": "repo"})
        self.city.enable()
        r = self.city.admin("init", "--write-root", str(self.city.write_root),
                            "--mode", "repo", "--hub", str(self.hub))
        assert r.returncode == 0, r.stderr

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_manifest_and_symlinks_false(self):
        self.assertTrue((self.city.write_root / mc.REPO_MANIFEST).is_file())
        proc = subprocess.run(["git", "-C", str(self.city.write_root), "config", "core.symlinks"],
                              capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "false")

    def test_git_fenced_to_manifest(self):
        with self.assertRaisesRegex(mc.MemoryError_, "refusing git"):
            mc.git(self.tmp, ["status"])

    def test_write_syncs_to_hub(self):
        self.city.remember("repo-fact", "travels the hub", scope="city")
        clone = self.tmp / "verify-clone"
        subprocess.run(["git", "clone", str(self.hub), str(clone)], capture_output=True, check=True)
        hits = list(clone.rglob("*repo-fact.md"))
        self.assertEqual(len(hits), 1)
        self.assertIn("travels the hub", hits[0].read_text())


class TestCommandShims(Base):
    def test_status_shim(self):
        env = self.city.env()
        env["GC_CITY_PATH"] = str(self.city.root)
        env["GC_PACK_DIR"] = str(PACK)
        r = subprocess.run(["sh", str(PACK / "commands" / "status" / "run.sh")],
                           capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["city"], "citya")

    def test_all_shims_executable(self):
        for run in (PACK / "commands").glob("*/run.sh"):
            self.assertTrue(os.access(run, os.X_OK), f"{run} not executable")


if __name__ == "__main__":
    unittest.main(verbosity=2)
