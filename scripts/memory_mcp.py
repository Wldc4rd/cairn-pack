#!/usr/bin/env python3
"""Memory MCP server (stdio JSON-RPC 2.0).

Vault-backed, scope-chained agent memory. Four tools:
  memory_remember(key, body, {scope, type, pinned, tags, bead}) — store/update
  memory_recall({budget})    — ranked agent→city→nation digest for THIS agent
  memory_search(query, {scope, limit}) — chain by default; scope "city" spans
                               sibling agent brains (missed promotions stay
                               discoverable); "nation" spans everything mounted
  memory_forget(key, {scope}) — delete (git history is the tombstone)

Security invariants (aligned with bartertown/postman):
- Default-off: every call re-checks the .gc/<pack>.enabled marker.
- Writes are secret-linted + banned-strings-linted, keys slug-sanitized, and
  path-confined to the memory root (symlink refusal included).
- Vault mode never runs git; repo mode fences git to the manifest-guarded clone.

Memory notes are the calling city's OWN content (one owner across the fleet),
so no untrusted-content envelope is applied — unlike cross-owner mail/forum
packs. If a future deployment mounts another OWNER's brain read-only, wrap
those reads before surfacing them.

Stdlib only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory_common as mc  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": mc.PACK_NAME, "version": "0.1.0"}


def tool_remember(city: Path, cfg: dict, args: dict) -> str:
    key = str(args.get("key", "")).strip()
    body = str(args.get("body", "")).strip()
    if not key or not body:
        raise mc.MemoryError_("'key' and 'body' are required")
    scope = str(args.get("scope", "agent")).strip() or "agent"
    path = mc.remember(
        city, cfg, key, body, scope=scope,
        type_=str(args.get("type", "memory")).strip() or "memory",
        pinned=bool(args.get("pinned", False)),
        tags=[str(t) for t in (args.get("tags") or [])],
        bead=str(args.get("bead", "")).strip(),
    )
    return f"Remembered [{key}] at {scope} scope ({path})."


def tool_recall(city: Path, cfg: dict, args: dict) -> str:
    budget = int(args.get("budget", 0)) or None
    return mc.recall_digest(city, cfg, budget_tokens=budget)


def tool_search(city: Path, cfg: dict, args: dict) -> str:
    query = str(args.get("query", "")).strip()
    hits = mc.search(city, cfg, query,
                     scope=str(args.get("scope", "chain")).strip() or "chain",
                     limit=int(args.get("limit", 20)))
    return json.dumps({"query": query, "hits": hits}, indent=2, ensure_ascii=False)


def tool_forget(city: Path, cfg: dict, args: dict) -> str:
    key = str(args.get("key", "")).strip()
    if not key:
        raise mc.MemoryError_("'key' is required")
    scope = str(args.get("scope", "agent")).strip() or "agent"
    ok = mc.forget(city, cfg, key, scope=scope)
    return f"Forgot [{key}] ({scope})." if ok else f"No such memory [{key}] at {scope} scope."


TOOLS = {
    "memory_remember": {
        "fn": tool_remember,
        "description": (
            "Store or update a durable memory note (markdown-in-git; timestamped, searchable, "
            "human-editable). Defaults to YOUR agent brain; scope 'city' shares it with this "
            "city's agents, scope 'nation' proposes/writes fleet-wide knowledge. Use type "
            "'handoff' (+bead) for in-flight resume state — it surfaces first in recall until "
            "the bead closes. Never store credentials: store a pointer (writes are secret-linted)."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "stable slug id; same key+scope updates in place"},
                "body": {"type": "string"},
                "scope": {"type": "string", "enum": ["agent", "city", "nation"], "default": "agent"},
                "type": {"type": "string", "enum": ["memory", "handoff"], "default": "memory"},
                "pinned": {"type": "boolean", "default": False},
                "tags": {"type": "array", "items": {"type": "string"}},
                "bead": {"type": "string", "description": "bead id a handoff tracks; it demotes when closed"},
            },
            "required": ["key", "body"],
        },
    },
    "memory_recall": {
        "fn": tool_recall,
        "description": (
            "Your ranked memory digest: agent→city→nation chain, most-specific-first with "
            "key-dedup; pinned first, then open handoffs, then newest; token-budgeted with an "
            "explicit 'N more' tail. Run at session start or before assuming context."
        ),
        "schema": {
            "type": "object",
            "properties": {"budget": {"type": "integer", "description": "token budget override"}},
        },
    },
    "memory_search": {
        "fn": tool_search,
        "description": (
            "Full-text search over memory notes. Default scope 'chain' = your agent brain + "
            "city + nation. Scope 'city' additionally spans SIBLING agent brains (find what a "
            "teammate learned but didn't promote); 'nation' spans every brain mounted locally."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string", "enum": ["chain", "city", "nation"], "default": "chain"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    },
    "memory_forget": {
        "fn": tool_forget,
        "description": "Delete a memory note you own (git history remains the tombstone).",
        "schema": {
            "type": "object",
            "properties": {
                "key": {"type": "string"},
                "scope": {"type": "string", "enum": ["agent", "city", "nation"], "default": "agent"},
            },
            "required": ["key"],
        },
    },
}


def _tools_list():
    return {"tools": [{"name": n, "description": s["description"], "inputSchema": s["schema"]}
                      for n, s in TOOLS.items()]}


def _call_tool(name: str, arguments: dict):
    spec = TOOLS.get(name)
    if not spec:
        raise mc.MemoryError_(f"unknown tool: {name}")
    city = mc.find_city_root()
    mc.require_enabled(city)  # default-deny on every call
    cfg = mc.load_config(city)
    text = spec["fn"](city, cfg, arguments or {})
    return {"content": [{"type": "text", "text": text}]}


def handle(req: dict):
    method = req.get("method", "")
    params = req.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    if method == "tools/list":
        return _tools_list()
    if method == "tools/call":
        try:
            return _call_tool(str(params.get("name", "")), params.get("arguments") or {})
        except mc.MemoryError_ as e:
            return {"content": [{"type": "text", "text": f"{mc.PACK_NAME} error: {e}"}], "isError": True}
        except Exception as e:  # noqa: BLE001 — surface, don't crash the server
            return {"content": [{"type": "text", "text":
                                 f"{mc.PACK_NAME} internal error: {type(e).__name__}: {e}"}],
                    "isError": True}
    if method == "ping":
        return {}
    if method.startswith("notifications/"):
        return None
    raise mc.MemoryError_(f"method not supported: {method}")


def main() -> int:
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" not in req:
            try:
                handle(req)
            except Exception:  # noqa: BLE001
                pass
            continue
        resp = {"jsonrpc": "2.0", "id": req["id"]}
        try:
            result = handle(req)
            resp["result"] = result if result is not None else {}
        except mc.MemoryError_ as e:
            resp["error"] = {"code": -32000, "message": str(e)}
        except Exception as e:  # noqa: BLE001
            resp["error"] = {"code": -32603, "message": f"{type(e).__name__}: {e}"}
        out.write(json.dumps(resp) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
