#!/usr/bin/env python3
"""responses-web-search — web search for Claude Code over the OpenAI Responses API.

Exposes one MCP tool, `web_search`. Each call sends the query to
`POST /v1/responses` with the built-in `web_search` tool, on the same relay
Claude Code already talks to, and returns the answer, the links it cites,
every URL the search consulted, and the record of each search call.

Credentials are the ones Claude Code itself uses: ANTHROPIC_BASE_URL and
ANTHROPIC_AUTH_TOKEN from the environment, or from the "env" block of
~/.claude/settings.json when the environment lacks them. Nothing is stored,
and the token is scrubbed from any error text before it is returned.

Register:

    claude mcp add responses-web-search --scope user -- python3 ~/.claude/mcp-servers/responses-web-search/server.py

Deliberate choices:
  * tool_choice is "required". Without it the model answers simple queries
    from memory and the response carries no web_search_call at all.
  * A response with no completed web_search_call is returned as an error,
    never as a search result.
  * Reasoning effort is the model's default unless RESPONSES_WEB_SEARCH_EFFORT
    is set. Relays can silently replace an unsupported value with the
    default, so the result reports the effort the API actually applied.
  * The relay sits behind Cloudflare, which rejects Python-urllib's default
    User-Agent (error 1010), so requests send their own.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SERVER_NAME = "responses-web-search"
SERVER_VERSION = "1.0.0"
MODEL = os.environ.get("RESPONSES_WEB_SEARCH_MODEL", "gpt-6.1-sol")
EFFORT = os.environ.get("RESPONSES_WEB_SEARCH_EFFORT", "").strip()  # empty: the model's own default
TIMEOUT_SEC = float(os.environ.get("RESPONSES_WEB_SEARCH_TIMEOUT_SEC", "180"))
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
USER_AGENT = f"{SERVER_NAME}/{SERVER_VERSION}"
INSTRUCTIONS = "Search the web to answer the query. Answer accurately and cite the pages you rely on."

_stdout_lock = threading.Lock()


class SearchError(Exception):
    pass


# ─── stdio framing (same as mcp-servers/codex-exec) ──────────────────────────

def _configure_stdio_for_mcp() -> None:
    sys.stdout = os.fdopen(sys.stdout.fileno(), "wb", buffering=0)
    sys.stdin = os.fdopen(sys.stdin.fileno(), "rb", buffering=0)


def send_message(message: Dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with _stdout_lock:
        sys.stdout.write(payload + b"\n")
        sys.stdout.flush()


def read_message() -> Optional[Dict[str, Any]]:
    """One JSON-RPC message per line (MCP stdio transport). None on EOF, {} on a blank/garbled line."""
    line = sys.stdin.readline()
    if not line:
        return None
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {}


# ─── Responses API call ──────────────────────────────────────────────────────

def load_credentials() -> Tuple[str, str]:
    """(base_url, token) from the environment, else from ~/.claude/settings.json's env block."""
    base = os.environ.get("ANTHROPIC_BASE_URL", "")
    token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
    if not (base and token):
        try:
            env = json.loads(SETTINGS_PATH.read_text(encoding="utf-8")).get("env") or {}
        except (OSError, ValueError):
            env = {}
        base = base or env.get("ANTHROPIC_BASE_URL", "")
        token = token or env.get("ANTHROPIC_AUTH_TOKEN", "")
    if not (base and token):
        raise SearchError(f"ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN not found in the environment or {SETTINGS_PATH}")
    return base.rstrip("/"), token


def responses_url(base: str) -> str:
    return base + ("/responses" if base.endswith("/v1") else "/v1/responses")


def call_responses(query: str, allowed_domains: List[str]) -> Dict[str, Any]:
    base, token = load_credentials()
    tool: Dict[str, Any] = {"type": "web_search"}
    if allowed_domains:
        tool["filters"] = {"allowed_domains": allowed_domains}
    body = {
        "model": MODEL,
        "instructions": INSTRUCTIONS,
        "input": query,
        "tools": [tool],
        "tool_choice": "required",
        "include": ["web_search_call.action.sources"],
        "store": False,
    }
    if EFFORT:
        body["reasoning"] = {"effort": EFFORT}
    request = urllib.request.Request(
        responses_url(base),
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SEC) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:1000].decode("utf-8", errors="replace")
        raise SearchError(f"HTTP {exc.code} from {base}: {detail}".replace(token, "***")) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise SearchError(f"request to {base} failed: {exc}".replace(token, "***")) from None


# ─── shaping the response ────────────────────────────────────────────────────

_TAGGED_URL = re.compile(r"https?://[^\s<>()\[\]\"']*utm_source=openai[^\s<>()\[\]\"']*")


def clean_url(url: str) -> str:
    """Drop the utm_source=openai tag the search tool appends to links."""
    parts = urllib.parse.urlsplit(url)
    if "utm_source=openai" not in parts.query:
        return url
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    query = urllib.parse.urlencode([p for p in pairs if p != ("utm_source", "openai")])
    return urllib.parse.urlunsplit(parts._replace(query=query))


def summarize(resp: Dict[str, Any]) -> Dict[str, Any]:
    if resp.get("error"):
        raise SearchError(f"Responses API error: {json.dumps(resp['error'], ensure_ascii=False)}")
    answer_parts: List[str] = []
    citations: List[Dict[str, str]] = []
    sources: List[str] = []
    searches: List[Dict[str, Any]] = []
    for item in resp.get("output") or []:
        kind = item.get("type")
        if kind == "web_search_call":
            action = item.get("action") or {}
            record: Dict[str, Any] = {"id": item.get("id"), "status": item.get("status"), "action": action.get("type")}
            for key in ("query", "url", "pattern"):
                if action.get(key):
                    record[key] = action[key]
            if action.get("queries") and action["queries"] != [action.get("query")]:
                record["queries"] = action["queries"]
            urls = [clean_url(s["url"]) for s in action.get("sources") or [] if s.get("url")]
            record["source_count"] = len(urls)
            searches.append(record)
            sources.extend(u for u in urls if u not in sources)
        elif kind == "message":
            for part in item.get("content") or []:
                if part.get("type") != "output_text":
                    continue
                answer_parts.append(part.get("text") or "")
                for ann in part.get("annotations") or []:
                    if ann.get("type") == "url_citation" and ann.get("url"):
                        url = clean_url(ann["url"])
                        if all(c["url"] != url for c in citations):
                            citations.append({"title": ann.get("title") or "", "url": url})

    status = resp.get("status")
    if not any(s["status"] == "completed" for s in searches):
        seen = [s["status"] for s in searches] or "none"
        raise SearchError(f"no completed web_search_call (response status {status}, search calls: {seen}); "
                          "the answer was not grounded in a web search")
    answer = _TAGGED_URL.sub(lambda m: clean_url(m.group(0)), "\n\n".join(answer_parts)).strip()
    if not answer:
        raise SearchError(f"search ran but the response has no answer text (status {status}, "
                          f"incomplete_details {json.dumps(resp.get('incomplete_details'))})")
    return {
        "answer": answer,
        "citations": citations,
        "sources": sources,
        "searches": searches,
        "model": resp.get("model"),
        "reasoning_effort": (resp.get("reasoning") or {}).get("effort"),
        "response_id": resp.get("id"),
        "status": status,
    }


# ─── MCP surface ─────────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "web_search",
        "description": (
            "Search the web through the OpenAI Responses API web_search tool. Every call performs a real search. "
            "Returns the answer, the source links it cites, all URLs the search consulted, and the search record "
            "(each web_search_call with its status and queries)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to search for: a question or keywords."},
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional. Restrict the search to these domains, e.g. [\"python.org\"]; subdomains are included.",
                },
            },
            "required": ["query"],
        },
    },
]


def error_response(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle_call(request: Dict[str, Any]) -> Dict[str, Any]:
    request_id = request.get("id")
    params = request.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    if name != "web_search":
        return error_response(request_id, -32601, f"unknown tool: {name}")
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        return error_response(request_id, -32602, "query is required")
    raw_domains = args.get("allowed_domains") or []
    if not isinstance(raw_domains, list):
        return error_response(request_id, -32602, "allowed_domains must be an array of strings")
    domains = [re.sub(r"^https?://", "", d.strip()).strip("/") for d in raw_domains if isinstance(d, str) and d.strip()]

    try:
        result = summarize(call_responses(query.strip(), domains))
    except SearchError as exc:
        text = f"web_search failed: {exc}"
        return {"jsonrpc": "2.0", "id": request_id,
                "result": {"isError": True, "content": [{"type": "text", "text": text}]}}
    # Claude Code hands the model structuredContent when present; the text block mirrors it for other clients
    text = json.dumps(result, ensure_ascii=False, indent=1)
    return {"jsonrpc": "2.0", "id": request_id,
            "result": {"content": [{"type": "text", "text": text}], "structuredContent": result}}


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    request_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params") or {}

    if request_id is None:
        return None  # notifications need no reply

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return handle_call(request)
    return error_response(request_id, -32601, f"method not found: {method}")


def respond(request: Dict[str, Any]) -> None:
    try:
        response = handle_request(request)
    except Exception:
        traceback.print_exc(file=sys.stderr)
        response = error_response(request.get("id"), -32603, "internal error; see the MCP server's stderr log")
    if response is not None:
        send_message(response)


def main() -> None:
    _configure_stdio_for_mcp()
    while True:
        request = read_message()
        if request is None:
            break
        if not request:
            continue
        if request.get("method") == "tools/call":
            # searches take seconds; run them off the read loop so parallel calls and pings are not queued
            threading.Thread(target=respond, args=(request,), daemon=True).start()
        else:
            respond(request)


if __name__ == "__main__":
    main()
