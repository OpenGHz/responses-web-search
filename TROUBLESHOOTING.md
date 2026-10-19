# Troubleshooting

English | [简体中文](TROUBLESHOOTING.zh-CN.md)

Find the entry that matches the error text. Run commands from this directory, and keep the token out of all output.

## `HTTP 403 ... error code: 1010`

Cloudflare is blocking by User-Agent. Python urllib's default UA gets blocked, so `server.py` already sends its own (`responses-web-search/<version>`). If a new relay blocks that too, change `USER_AGENT` in `server.py` to a common client format such as `curl/8.5.0` (verified to pass on the author's relay).

## `HTTP 503 ... no_available_providers`

The relay doesn't route this model on `/v1/responses`, so switch models:

1. List the models this token can see:
   ```bash
   python3 -c 'import server,json,urllib.request as u
   b,t=server.load_credentials()
   r=u.Request(b+"/v1/models",headers={"Authorization":"Bearer "+t,"User-Agent":server.USER_AGENT})
   print([m["id"] for m in json.load(u.urlopen(r,timeout=30)).get("data",[])])'
   ```
2. That list is incomplete. On the author's relay it showed only `deepseek-*`, all of which returned 503 on `/v1/responses`, while `gpt-6.1-sol`, which wasn't listed, worked. So also try GPT model names, such as the one in the user's Codex config (`model` in `~/.codex/config.toml`).
3. Try them one at a time: `RESPONSES_WEB_SEARCH_MODEL=<model> python3 helper.py check`. Once one PASSes, run `register` from README step 3 with the same variable.

If `RESPONSES_WEB_SEARCH_EFFORT` is set, unset it and try again. On the author's relay an unsupported reasoning effort also produces this 503 (`service_unavailable_error`).

## `requested effort ... but the API applied ...`

The model doesn't support that reasoning effort, and the relay substituted the default. Use a value the model supports (gpt-6.1-sol supports `low`, `medium`, `high`, `xhigh`, `max`), or leave `RESPONSES_WEB_SEARCH_EFFORT` unset.

## `HTTP 401`, or a `403` other than 1010

The token is invalid or lacks permission. First confirm that Claude Code itself can chat normally. The token comes from `env.ANTHROPIC_AUTH_TOKEN` in `~/.claude/settings.json`, or from the environment variable of the same name. When debugging, compare only its length or its first 3 characters; never print the whole token.

## `no completed web_search_call`

The request succeeded, but the model never actually searched, which means this model or relay doesn't execute web_search. Switch models as described above. If none works, it is unsupported: stop and tell the user.

## `ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN not found`

Claude Code on this machine doesn't reach a relay through settings.json's env or environment variables; for example, it is signed in to an official account via OAuth. This MCP server depends on a relay, so tell the user how things stand and stop.

## Not Connected after `register`

- Run `python3 server.py` by hand. Normally it waits quietly for input (Ctrl-C to quit). If it prints an error, fix that error.
- `python3` may not be on Claude Code's PATH. Run `claude mcp remove responses-web-search -s user`, then register with absolute paths:
  `claude mcp add responses-web-search --scope user -- "$(command -v python3)" "$PWD/server.py"`

## `builtin` stays INCONCLUSIVE

Claude didn't call WebSearch as asked. Run it by hand:
`claude -p 'Use WebSearch once to search for latest Python release' --allowedTools WebSearch --output-format stream-json --verbose`
then find `searchCount` in the output's `tool_use_result`; 0 means no real search ran.

## Claude used another tool first in `e2e`

WebFetch, or a skill whose description claims search requests, may be called first. Under `claude -p` they have no permission and fail; in an interactive session they ask the user for approval. Step 6 PASSes as long as the MCP tool is eventually called and succeeds. Whether to restrict the other tools is the user's decision.

## Background: why Claude doesn't search by itself

Measured in 2026-10 on the author's relay:
- **Claude Code's built-in WebSearch:** returns `is_error=false` but `searchCount: 0, results: []`, a silent failure. Claude takes it as "nothing found" and keeps retrying with reworded queries, which is where the repeated failures come from.
- **Calling the web_search server tool on `/v1/messages` directly:** every direct request for a Claude model returned 503, even without tools, while Claude Code itself worked at the same moment; the relay appears to admit only Claude Code clients. Do not impersonate the Claude Code client to get around this.
- Results may differ on another relay, which is why README step 4 always measures.
