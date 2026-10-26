#!/usr/bin/env python3
"""Install and verify helper for the responses-web-search MCP server.

Each subcommand is one step of README.md. None of them prints the API token.

    python3 helper.py check [QUERY]   one real search through server.py's own code path
    python3 helper.py register        back up ~/.claude.json, register the server at user scope
    python3 helper.py builtin         does Claude Code's built-in WebSearch return results here?
    python3 helper.py deny-websearch  back up ~/.claude/settings.json, add WebSearch to permissions.deny
    python3 helper.py e2e             a fresh `claude -p` session searches through the MCP tool
    python3 helper.py leaks           scan the files this setup touches for the token

Exit status 0 is PASS. `builtin` exits 0 when the built-in tool works, 2 when it is
broken, 3 when it is not visible (already denied), 4 when the run was inconclusive.
"""
from __future__ import annotations

import difflib
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import server  # noqa: E402

NAME = server.SERVER_NAME
MCP_TOOL = f"mcp__{NAME}__web_search"
CLAUDE_JSON = Path.home() / ".claude.json"
SETTINGS = server.SETTINGS_PATH


# ─── shared ──────────────────────────────────────────────────────────────────

def backup(path: Path) -> Path:
    """Copy path to a timestamped sibling, never overwriting an earlier backup."""
    stamp = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    dest, n = stamp, 1
    while dest.exists():
        dest, n = stamp.with_name(f"{stamp.name}-{n}"), n + 1
    shutil.copy2(path, dest)
    return dest


def secrets() -> List[str]:
    """Every token value this setup could leak: Claude Code's (environment, settings.json) and the Codex key."""
    found = [os.environ.get("ANTHROPIC_AUTH_TOKEN", "")]
    try:
        found.append((json.loads(SETTINGS.read_text(encoding="utf-8")).get("env") or {}).get("ANTHROPIC_AUTH_TOKEN", ""))
    except (OSError, ValueError):
        pass
    try:
        found.append(json.loads((server.CODEX_HOME / "auth.json").read_text(encoding="utf-8")).get("OPENAI_API_KEY") or "")
    except (OSError, ValueError):
        pass
    return [s for s in set(found) if s]


def mask(text: str) -> str:
    for s in secrets():
        text = text.replace(s, "***")
    return text


def run_claude(prompt: str, allowed: str, disallowed: Optional[str] = None) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Run a fresh `claude -p` session; return (tools it could see, tool calls with their results)."""
    cmd = ["claude", "-p", prompt, "--allowedTools", allowed, "--output-format", "stream-json", "--verbose"]
    if disallowed:
        cmd += ["--disallowedTools", disallowed]
    workdir = tempfile.mkdtemp(prefix="rws-")
    try:
        proc = subprocess.run(cmd, cwd=workdir, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    tools: List[str] = []
    calls: List[Dict[str, Any]] = []
    by_id: Dict[str, Dict[str, Any]] = {}
    for line in proc.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init":
            tools = event.get("tools") or []
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        for block in message.get("content") if isinstance(message.get("content"), list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                call = {"name": block.get("name"), "input": block.get("input"), "result": None}
                by_id[block.get("id")] = call
                calls.append(call)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                body = block.get("content")
                text = body if isinstance(body, str) else "".join(
                    x.get("text", "") for x in body or [] if isinstance(x, dict))
                by_id[block["tool_use_id"]]["result"] = {
                    "is_error": bool(block.get("is_error")), "text": text, "meta": event.get("tool_use_result")}
    if proc.returncode != 0 or not tools:
        print(f"claude -p exited {proc.returncode}; stderr: {mask(proc.stderr.strip())[:500]}")
    return tools, calls


def print_calls(calls: List[Dict[str, Any]]) -> None:
    for call in calls:
        result = call["result"] or {}
        print(f"  call {call['name']} {json.dumps(call['input'], ensure_ascii=False)[:100]}")
        print(f"       -> is_error={result.get('is_error')} {mask(result.get('text') or '')[:120]!r}")


# ─── steps ───────────────────────────────────────────────────────────────────

def cmd_check(args: List[str]) -> int:
    query = " ".join(args) or "What is the latest stable Python release? Cite the source."
    try:
        base, _ = server.load_credentials()
        print(f"auth {server.AUTH}, model {server.MODEL}, effort {server.EFFORT or '(model default)'}, "
              f"endpoint {server.responses_url(base)}")
        result = server.summarize(server.call_responses(query, []))
    except server.SearchError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"answer: {result['answer'][:400]}")
    for c in result["citations"]:
        print(f"  cited: {c['url']}")
    for s in result["searches"]:
        print(f"  search [{s['status']}] {s['action']}: {s.get('queries') or s.get('query') or s.get('url')} "
              f"({s['source_count']} sources)")
    applied = result["reasoning_effort"]
    print(f"reasoning effort applied: {applied}")
    if server.EFFORT and applied != server.EFFORT:
        print(f"FAIL: requested effort {server.EFFORT!r} but the API applied {applied!r}; "
              "this model does not support that value")
        return 1
    done = sum(s["status"] == "completed" for s in result["searches"])
    print(f"PASS: {done} completed web_search_call(s), {len(result['sources'])} sources consulted")
    return 0


def cmd_register(args: List[str]) -> int:
    server_py = str(HERE / "server.py")
    before = json.loads(CLAUDE_JSON.read_text(encoding="utf-8")) if CLAUDE_JSON.exists() else {}
    keys = ("RESPONSES_WEB_SEARCH_AUTH", "RESPONSES_WEB_SEARCH_MODEL", "RESPONSES_WEB_SEARCH_EFFORT",
            "RESPONSES_WEB_SEARCH_ALLOWED_MODELS")
    env = {k: os.environ[k] for k in keys if os.environ.get(k)}
    existing = (before.get("mcpServers") or {}).get(NAME)
    if existing and existing.get("args") == [server_py] and (existing.get("env") or {}) == env:
        print(f"already registered with {server_py} and env {env}; skipping add")
    elif existing:
        print(mask(f"FAIL: {NAME} is registered with different settings {existing} (wanted env {env}); "
                   f"remove it first: claude mcp remove {NAME} -s user"))
        return 1
    else:
        if CLAUDE_JSON.exists():
            print(f"backup: {backup(CLAUDE_JSON)}")
        cmd = ["claude", "mcp", "add", NAME, "--scope", "user"]
        for key, value in env.items():
            cmd += ["-e", f"{key}={value}"]
        cmd += ["--", "python3", server_py]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        print(mask(proc.stdout + proc.stderr).strip())
        if proc.returncode != 0:
            print("FAIL: claude mcp add failed")
            return 1
        if before:
            after = json.loads(CLAUDE_JSON.read_text(encoding="utf-8"))
            changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
            print(f"changed top-level keys in {CLAUDE_JSON}: {changed} (expected: ['mcpServers'])")
        else:
            print(f"{CLAUDE_JSON} did not exist; claude created it (first-run keys are expected)")
    proc = subprocess.run(["claude", "mcp", "get", NAME], capture_output=True, text=True)
    print(mask(proc.stdout + proc.stderr).strip())
    if "Connected" not in proc.stdout:
        print("FAIL: server is not Connected")
        return 1
    print("PASS: registered and Connected")
    return 0


def cmd_builtin(args: List[str]) -> int:
    tools, calls = run_claude(
        'Use the WebSearch tool exactly once to search for "latest stable Python release", then answer in one sentence.',
        "WebSearch", MCP_TOOL)
    print_calls(calls)
    if tools and "WebSearch" not in tools:
        print("NOT VISIBLE: built-in WebSearch is not in the tool list (already denied?)")
        return 3
    searches = [c for c in calls if c["name"] == "WebSearch" and c["result"]]
    if not searches:
        print("INCONCLUSIVE: the session never completed a WebSearch call")
        return 4
    result = searches[0]["result"]
    meta = result["meta"] if isinstance(result["meta"], dict) else {}
    count, results = meta.get("searchCount"), meta.get("results") or []
    print(f"WebSearch: is_error={result['is_error']} searchCount={count} results={len(results)}")
    if result["is_error"] or count == 0 or (count is None and not results):
        print("BROKEN: the built-in WebSearch ran no search; disable it (README step 5)")
        return 2
    print("WORKS: the built-in WebSearch returns results on this machine")
    return 0


def cmd_deny_websearch(args: List[str]) -> int:
    raw = SETTINGS.read_text(encoding="utf-8") if SETTINGS.exists() else "{}\n"
    data = json.loads(raw)
    deny = data.setdefault("permissions", {}).setdefault("deny", [])
    if "WebSearch" in deny:
        print(f"PASS: WebSearch is already in permissions.deny of {SETTINGS}")
        return 0
    if SETTINGS.exists():
        print(f"backup: {backup(SETTINGS)}")
    else:
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    deny.append("WebSearch")
    m = re.search(r'\n([ \t]+)"', raw)
    SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=m.group(1) if m else "  ") + "\n", encoding="utf-8")
    new = SETTINGS.read_text(encoding="utf-8")
    json.loads(new)
    diff = difflib.unified_diff(mask(raw).splitlines(), mask(new).splitlines(), "before", "after", lineterm="")
    print("\n".join(diff))
    print(f"PASS: WebSearch added to permissions.deny of {SETTINGS}")
    return 0


def cmd_e2e(args: List[str]) -> int:
    tools, calls = run_claude("Search the web: what is the latest stable Python release? Cite a source link.", MCP_TOOL)
    print(f"tools visible: WebSearch={'WebSearch' in tools} {MCP_TOOL}={MCP_TOOL in tools}")
    print_calls(calls)
    if MCP_TOOL not in tools:
        print("FAIL: the MCP tool is not visible to Claude Code")
        return 1
    for call in calls:
        result = call["result"] or {}
        if call["name"] != MCP_TOOL or result.get("is_error"):
            continue
        try:
            payload = json.loads(result.get("text") or "{}")
        except ValueError:
            continue
        if any(s.get("status") == "completed" for s in payload.get("searches") or []):
            first = calls[0]["name"]
            print(f"PASS: the session searched through {MCP_TOOL} "
                  f"(first tool called: {first}, reasoning effort applied: {payload.get('reasoning_effort')})")
            if "WebSearch" in tools:
                print("NOTE: built-in WebSearch is still visible; Claude may try it first")
            return 0
    print("FAIL: no successful MCP search in the session")
    return 1


def cmd_leaks(args: List[str]) -> int:
    tokens = secrets()
    if not tokens:
        print("FAIL: no token found to scan for (Claude Code or Codex)")
        return 1
    paths = [p for p in HERE.rglob("*") if p.is_file()]
    paths += [CLAUDE_JSON, *CLAUDE_JSON.parent.glob(".claude.json.bak-*")]
    for cache in ("Library/Caches/claude-cli-nodejs", ".cache/claude-cli-nodejs"):
        paths += [Path(p) for p in glob.glob(str(Path.home() / cache / "*" / f"mcp-logs-{NAME}" / "*"))]
    hits = 0
    for path in paths:
        if not path.is_file():
            continue
        count = sum(path.read_bytes().decode("utf-8", "replace").count(t) for t in tokens)
        if count:
            print(f"LEAK: {count} hit(s) in {path}")
            hits += count
    print(f"scanned {len(paths)} files")
    if hits:
        print("FAIL: token found in the files above")
        return 1
    print("PASS: token appears in none of them")
    return 0


COMMANDS = {
    "check": cmd_check,
    "register": cmd_register,
    "builtin": cmd_builtin,
    "deny-websearch": cmd_deny_websearch,
    "e2e": cmd_e2e,
    "leaks": cmd_leaks,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(64)
    sys.exit(COMMANDS[sys.argv[1]](sys.argv[2:]))


if __name__ == "__main__":
    main()
